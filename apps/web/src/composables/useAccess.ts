import { readonly, ref } from "vue";
import { ApiError } from "@/api/errors";
import { protectedResult } from "@/api/protectedResult";
import type { AccessApi, Grant, GrantTarget, Role, RoleInput, UserPatch, UserSummary } from "@/api/accessTypes";
import type { AdminDocument } from "@/api/documentTypes";
import type { SessionState } from "@/composables/useSession";
function accessError(error: ApiError): string {
  switch (error.status) {
    case 404: return "Объект недоступен. Обновите данные перед повторным действием.";
    case 409: return "Запись уже существует или была изменена. Обновите данные.";
    case 422: return "Проверьте заполнение полей.";
    default: return "Операция не подтверждена. Обновите данные перед повторным действием.";
  }
}

export function createAccessState(api: AccessApi, session: SessionState) {
  const grants = ref<readonly Grant[]>([]), users = ref<readonly UserSummary[]>([]);
  const roles = ref<readonly Role[]>([]), documents = ref<readonly AdminDocument[]>([]);
  const members = ref<readonly UserSummary[]>([]), message = ref("");
  const selectedDocumentId = ref<string | null>(null), selectedRoleId = ref<string | null>(null);
  const busy = ref(false);
  let epoch = 0;
  let controller = new AbortController();
  let disposed = false;
  const isActive = () => !disposed;
  function clear() {
    epoch += 1; controller.abort(); controller = new AbortController();
    grants.value = []; users.value = []; roles.value = []; documents.value = []; members.value = [];
    selectedDocumentId.value = null; selectedRoleId.value = null; message.value = ""; busy.value = false;
  }
  const unsubscribe = session.onClear(clear);

  async function run<T>(operation: (signal: AbortSignal, token: string) => Promise<T>, apply: (value: T) => void): Promise<boolean> {
    if (!isActive() || session.user.value?.is_admin !== true) return false;
    epoch += 1;
    const current = epoch;
    controller.abort(); controller = new AbortController();
    busy.value = true; message.value = "";
    try {
      const result = await session.runProtected((signal, token) => protectedResult(() =>
        operation(AbortSignal.any([signal, controller.signal]), token)));
      if (current !== epoch) return false;
      if (!result.ok) throw result.error;
      apply(result.value);
      return true;
    } catch (error) {
      if (current !== epoch) return false;
      if (!(error instanceof ApiError)) throw error;
      message.value = accessError(error);
      return false;
    } finally { if (current === epoch) busy.value = false; }
  }

  async function snapshot(signal: AbortSignal, documentId: string | null, roleId: string | null) {
    const [people, groups, registry] = await Promise.all([api.users(signal), api.roles(signal), api.documents(signal)]);
    const document = registry.find(item => item.id === documentId)?.id ?? null;
    const role = groups.find(item => item.id === roleId)?.id ?? null;
    const [permissions, membership] = await Promise.all([
      document === null ? Promise.resolve([]) : api.grants(document, signal),
      role === null ? Promise.resolve([]) : api.members(role, signal),
    ]);
    return { people, groups, registry, document, role, permissions, membership };
  }
  function applySnapshot(value: Awaited<ReturnType<typeof snapshot>>) {
    users.value = value.people; roles.value = value.groups; documents.value = value.registry;
    grants.value = value.permissions; members.value = value.membership;
    selectedDocumentId.value = value.document; selectedRoleId.value = value.role;
  }
  async function load(documentId?: string) {
    const document = documentId ?? selectedDocumentId.value;
    const role = selectedRoleId.value;
    clear();
    await run(signal => snapshot(signal, document, role), applySnapshot);
  }
  async function selectDocument(id: string) {
    if (busy.value) return;
    grants.value = []; selectedDocumentId.value = documents.value.find(item => item.id === id)?.id ?? null;
    const target = selectedDocumentId.value;
    if (target !== null) await run(signal => api.grants(target, signal), value => { grants.value = value; });
  }
  async function selectRole(id: string) {
    if (busy.value) return;
    members.value = []; selectedRoleId.value = roles.value.find(item => item.id === id)?.id ?? null;
    const target = selectedRoleId.value;
    if (target !== null) await run(signal => api.members(target, signal), value => { members.value = value; });
  }
  async function mutate(operation: (signal: AbortSignal, token: string) => Promise<unknown>) {
    if (busy.value) return false;
    const document = selectedDocumentId.value, role = selectedRoleId.value;
    return run(async (signal, token) => {
      await operation(signal, token);
      return snapshot(signal, document, role);
    }, applySnapshot);
  }
  async function createGrant(target: GrantTarget) {
    const document = selectedDocumentId.value;
    return document !== null && await mutate((signal, token) => api.createGrant(document, target, token, signal));
  }
  async function revokeGrant(id: string) {
    const document = selectedDocumentId.value;
    return document !== null && grants.value.some(item => item.id === id) &&
      await mutate((signal, token) => api.revokeGrant(document, id, token, signal));
  }
  async function addMember(userId: string) {
    const role = selectedRoleId.value;
    return role !== null && await mutate((signal, token) => api.addMember(role, userId, token, signal));
  }
  async function removeMember(userId: string) {
    const role = selectedRoleId.value;
    return role !== null && members.value.some(item => item.id === userId) &&
      await mutate((signal, token) => api.removeMember(role, userId, token, signal));
  }
  const updateUser = (userId: string, patch: UserPatch) => mutate((signal, token) => api.updateUser(userId, patch, token, signal));
  const createRole = (value: RoleInput) => mutate((signal, token) => api.createRole(value, token, signal));
  async function renameRole(name: string) {
    const role = selectedRoleId.value;
    return role !== null && await mutate((signal, token) => api.renameRole(role, name, token, signal));
  }
  return { grants: readonly(grants), users: readonly(users), roles: readonly(roles), documents: readonly(documents),
    members: readonly(members), message: readonly(message), busy: readonly(busy),
    selectedDocumentId: readonly(selectedDocumentId), selectedRoleId: readonly(selectedRoleId),
    load, selectDocument, selectRole, revokeGrant, createGrant, addMember, removeMember, updateUser, createRole, renameRole,
    dispose: () => { disposed = true; clear(); unsubscribe(); } };
}
