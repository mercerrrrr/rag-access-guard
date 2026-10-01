import { expect, it, vi } from "vitest";
import { createAccessState } from "@/composables/useAccess";
import { createSessionState } from "@/composables/useSession";
import { accessApi, accessRole, accessUser, directGrant, roleGrant } from "@/test/access";
import { document as adminDocument } from "@/test/documents";
import { deferred, sessionApi, student } from "@/test/session";
import { ApiError } from "@/api/errors";
import type { AccessApi, UserSummary } from "@/api/accessTypes";
async function setup(overrides: Partial<AccessApi> = {}) {
  const session = createSessionState(sessionApi({ me: () => Promise.resolve({ user: { ...student, is_admin: true } }) }));
  await session.refresh();
  const state = createAccessState(accessApi(overrides), session); await state.load();
  return { state, session };
}
it("role_revoke_does_not_claim_direct_grant_removed", async () => {
  let rows = [directGrant, roleGrant];
  const { state } = await setup({ grants: () => Promise.resolve(rows),
    revokeGrant: (_doc, id) => { rows = rows.filter(row => row.id !== id); return Promise.resolve(); } });
  await state.selectDocument(adminDocument.id);
  await state.revokeGrant(roleGrant.id);
  expect(state.grants.value).toEqual([directGrant]);
  expect(state.message.value).not.toContain("Доступ полностью отозван"); state.dispose();
});
it("failed_revoke_keeps_confirmed_row", async () => {
  const { state } = await setup({ revokeGrant: () => Promise.reject(new ApiError(503)) });
  await state.selectDocument(adminDocument.id); await state.revokeGrant(directGrant.id);
  expect(state.grants.value).toContainEqual(directGrant);
  expect(state.message.value).not.toBe(""); state.dispose();
});
it("expired_admin_session_clears_management_data", async () => {
  const { state, session } = await setup({ createGrant: () => Promise.reject(new ApiError(401)) });
  await state.selectDocument(adminDocument.id); await state.createGrant({ user_id: student.id });
  expect(session.status.value).toBe("anonymous");
  expect(state.users.value).toEqual([]); expect(state.grants.value).toEqual([]); state.dispose();
});
it("duplicate_grant_shows_server_conflict", async () => {
  const { state } = await setup({ createGrant: () => Promise.reject(new ApiError(409)) });
  await state.selectDocument(adminDocument.id); await state.createGrant({ user_id: student.id });
  expect(state.message.value).toContain("уже существует");
  expect(state.grants.value).toHaveLength(2); state.dispose();
});
it("late_list_cannot_restore_management_after_logout", async () => {
  const pending = deferred<readonly UserSummary[]>();
  const { state, session } = await setup();
  const late = createAccessState(accessApi({ users: () => pending.promise }), session);
  const loading = late.load(); await session.signOut(); pending.resolve([{ ...student, is_active: true }]); await loading;
  expect(late.users.value).toEqual([]); expect(late.documents.value).toEqual([]);
  late.dispose(); state.dispose();
});
it("query_document_cannot_select_unknown_metadata", async () => {
  const grants = vi.fn(() => Promise.resolve([directGrant]));
  const { state } = await setup({ grants });
  await state.load("00000000-0000-4000-8000-000000000099");
  expect(state.selectedDocumentId.value).toBeNull(); expect(grants).not.toHaveBeenCalled(); state.dispose();
});
it("admin_loss_revalidates_identity_and_clears_every_list", async () => {
  let admin = true;
  const session = createSessionState(sessionApi({ me: () => Promise.resolve({ user: { ...student, is_admin: admin } }) }));
  await session.refresh();
  const state = createAccessState(accessApi({ revokeGrant: () => { admin = false; return Promise.reject(new ApiError(403)); } }), session);
  await state.load(adminDocument.id); await state.revokeGrant(directGrant.id);
  expect(session.user.value?.is_admin).toBe(false);
  expect(state.users.value).toEqual([]); expect(state.roles.value).toEqual([]); expect(state.documents.value).toEqual([]);
  expect(state.grants.value).toEqual([]); state.dispose();
});
it("successful_membership_and_role_changes_refetch_server_state", async () => {
  const members = vi.fn(() => Promise.resolve([accessUser]));
  const addMember = vi.fn(() => Promise.resolve()), removeMember = vi.fn(() => Promise.resolve());
  const createRole = vi.fn(() => Promise.resolve(accessRole)), renameRole = vi.fn(() => Promise.resolve(accessRole));
  const updateUser = vi.fn(() => Promise.resolve(accessUser));
  const { state } = await setup({ members, addMember, removeMember, createRole, renameRole, updateUser });
  await state.selectRole(accessRole.id); await state.addMember(accessUser.id); await state.removeMember(accessUser.id);
  await state.createRole({ code: "new_role", display_name: "Новая" }); await state.renameRole("Читатели");
  await state.updateUser(accessUser.id, { is_active: false });
  expect(members).toHaveBeenCalledTimes(6);
  expect(addMember).toHaveBeenCalledWith(accessRole.id, accessUser.id, expect.any(String), expect.any(AbortSignal));
  expect(removeMember).toHaveBeenCalledTimes(1); expect(createRole).toHaveBeenCalledTimes(1);
  expect(renameRole).toHaveBeenCalledTimes(1); expect(updateUser).toHaveBeenCalledTimes(1); state.dispose();
});
it("pending_mutation_cannot_be_submitted_twice", async () => {
  const pending = deferred<undefined>();
  const revokeGrant = vi.fn(() => pending.promise);
  const { state } = await setup({ revokeGrant }); await state.selectDocument(adminDocument.id);
  const first = state.revokeGrant(directGrant.id); await state.revokeGrant(directGrant.id);
  expect(revokeGrant).toHaveBeenCalledTimes(1); pending.resolve(undefined); await first; state.dispose();
});
