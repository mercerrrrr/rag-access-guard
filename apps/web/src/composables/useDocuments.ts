import { computed, readonly, ref } from "vue";
import { ApiError } from "@/api/errors";
import { protectedResult } from "@/api/protectedResult";
import type { AllowedDocument, DocumentEntry, DocumentText, DocumentUpload, DocumentVersion, DocumentsApi,
  DocumentUpdate, VersionUpload } from "@/api/documentTypes";
import type { SessionState } from "@/composables/useSession";

function documentError(error: ApiError): string {
  switch (error.status) {
    case 404: return "Документ или версия недоступны. Обновите реестр.";
    case 409: return "Документ изменился во время операции. Обновите реестр перед повторной загрузкой.";
    case 413: return "Файл превышает допустимый размер 10 МиБ.";
    case 415: return "Тип файла не поддерживается. Используйте TXT, Markdown или текстовый PDF.";
    case 422: return "Файл или название не прошли проверку. PDF должен содержать текст и не больше 200 страниц.";
    case 503: return "Обработка недоступна. Проверьте список версий перед повторной загрузкой.";
    default: return "Операция не подтверждена. Обновите реестр перед повторным действием.";
  }
}

export function createDocumentsState(api: DocumentsApi, session: SessionState) {
  const items = ref<readonly DocumentEntry[]>([]);
  const allowed = ref<readonly AllowedDocument[]>([]);
  const selected = ref<DocumentEntry | null>(null);
  const versions = ref<readonly DocumentVersion[]>([]);
  const content = ref<DocumentText | null>(null);
  const busy = ref(false);
  const message = ref("");
  let epoch = 0;
  let controller = new AbortController();
  const canReadContent = computed(() => selected.value !== null && allowed.value.some(
    (entry) => entry.id === selected.value?.id && entry.active_version_id === selected.value.active_version_id));

  function clearSelection() { selected.value = null; versions.value = []; content.value = null; }
  function clear() {
    epoch += 1;
    controller.abort();
    controller = new AbortController();
    items.value = []; allowed.value = []; clearSelection(); busy.value = false; message.value = "";
  }
  const unsubscribe = session.onClear(clear);

  async function run<T>(operation: (signal: AbortSignal, token: string) => Promise<T>, apply: (value: T) => void) {
    epoch += 1;
    const current = epoch;
    controller.abort();
    controller = new AbortController();
    content.value = null; message.value = ""; busy.value = true;
    try {
      const result = await session.runProtected((signal, token) => protectedResult(() =>
        operation(AbortSignal.any([signal, controller.signal]), token)));
      if (current !== epoch) return;
      if (!result.ok) throw result.error;
      apply(result.value);
    } catch (error) {
      if (current !== epoch) return;
      if (!(error instanceof ApiError)) throw error;
      message.value = documentError(error);
    } finally { if (current === epoch) busy.value = false; }
  }

  async function refresh() {
    clear();
    await run((signal) => load(signal), applySnapshot);
  }

  async function load(signal: AbortSignal, selectedId: string | null = null) {
    const admin = session.user.value?.is_admin === true;
    const [granted, administrative] = await Promise.all([api.allowed(signal), admin ? api.admin(signal) : Promise.resolve([])]);
    const entries = admin ? administrative : granted;
    const selection = entries.find((item) => item.id === selectedId) ?? null;
    const history = admin && selection !== null ? await api.versions(selection.id, signal) : [];
    return { granted, entries, selection, history };
  }

  function applySnapshot(snapshot: Awaited<ReturnType<typeof load>>) {
    allowed.value = snapshot.granted; items.value = snapshot.entries;
    selected.value = snapshot.selection; versions.value = snapshot.history;
  }

  async function select(id: string) {
    const entry = items.value.find((item) => item.id === id) ?? null;
    clearSelection();
    selected.value = entry;
    await run((signal) => entry !== null && session.user.value?.is_admin
      ? api.versions(entry.id, signal) : Promise.resolve([]), (value) => { versions.value = value; });
  }

  async function read() {
    const entry = selected.value;
    if (!canReadContent.value || entry === null || entry.active_version_id === null) return;
    const target = { documentId: entry.id, versionId: entry.active_version_id };
    await run((signal) => api.text(target, signal),
      (value) => { content.value = value; });
  }

  async function mutate(operation: (signal: AbortSignal, token: string) => Promise<string>, selectionId: string | null) {
    clear();
    await run(async (signal, token) => {
      const result = await protectedResult(() => operation(signal, token));
      const snapshot = await load(signal, result.ok ? result.value : selectionId);
      return { snapshot, error: result.ok ? "" : documentError(result.error) };
    }, ({ snapshot, error }) => { applySnapshot(snapshot); message.value = error; });
  }
  const upload = (value: DocumentUpload) => mutate(async (signal, token) => (await api.upload(value, token, signal)).id, null);
  const uploadVersion = (value: VersionUpload) => mutate(async (signal, token) => {
    await api.uploadVersion(value, token, signal);
    return value.documentId;
  }, value.documentId);
  const update = (value: DocumentUpdate) => mutate(async (signal, token) => (await api.update(value, token, signal)).id, value.documentId);
  function closeContent() {
    epoch += 1; controller.abort(); controller = new AbortController(); content.value = null; busy.value = false;
  }
  return { items: readonly(items), versions: readonly(versions), content: readonly(content),
    selected: readonly(selected), busy: readonly(busy), message: readonly(message), canReadContent,
    refresh, select, read, upload, uploadVersion, update, closeContent, dispose: () => { clear(); unsubscribe(); } };
}
