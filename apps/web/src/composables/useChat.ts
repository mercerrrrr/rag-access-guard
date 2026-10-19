import { computed, readonly, ref } from "vue";
import { ApiError } from "@/api/errors";
import { protectedResult } from "@/api/protectedResult";
import type { ChatApi, ThreadDetail, ThreadView } from "@/api/types";
import { createChatSubmission } from "@/composables/chatSubmission";
import { createSourceInspector } from "@/composables/useSourceInspector";
import type { SessionState } from "@/composables/useSession";

export function createChatState(api: ChatApi, session: SessionState) {
  const thread = ref<ThreadDetail | null>(null);
  const threads = ref<readonly ThreadView[]>([]);
  const loading = ref(false);
  const message = ref("");
  const selectedTurn = ref<string | null>(null);
  const inspector = createSourceInspector(api, session);
  let epoch = 0;
  let controller = new AbortController();
  let currentId: string | null = null;
  const submission = createChatSubmission(api, session, {
    getThread: () => thread.value, getId: () => currentId,
    setThread: (value) => { thread.value = value; },
    setMessage: (value) => { message.value = value; },
    clearSources: () => { inspector.clear(); selectedTurn.value = null; },
    missing: () => { resetView(); submission.discardRetry(); message.value = "Диалог недоступен."; },
    refresh: loadThread, selectTurn, listThreads,
  });
  const { sending, retryRequest, pendingSubmission, completedSubmission, progress } = submission;
  const sources = computed(() => {
    const turn = thread.value?.turns.find((item) => item.id === selectedTurn.value);
    return turn?.state === "available" ? turn.sources : [];
  });

  function resetView() {
    epoch += 1;
    controller.abort();
    controller = new AbortController();
    inspector.clear();
    selectedTurn.value = null;
    thread.value = null;
    loading.value = false;
    submission.reset();
    message.value = "";
  }
  function clear() {
    resetView();
    currentId = null;
    submission.discardRetry();
    threads.value = [];
  }
  const unsubscribe = session.onClear(clear);
  function selectTurn(id: string) {
    inspector.clear();
    selectedTurn.value = thread.value?.turns.some((turn) => turn.id === id && turn.state === "available")
      ? id : null;
  }
  async function listThreads() {
    const current = epoch;
    try {
      const result = await session.runProtected((signal) => protectedResult(() => api.list(signal)));
      if (current !== epoch) return;
      if (result.ok) threads.value = result.value;
      else message.value = "Не удалось загрузить список диалогов. Повторите обновление.";
    } catch (error) { if (!(error instanceof ApiError)) throw error; }
  }
  async function loadThread(id: string, discardRetry = false) {
    if (id !== currentId) submission.discardRetry();
    resetView();
    currentId = id;
    const current = epoch;
    loading.value = true;
    try {
      const result = await session.runProtected((signal) => protectedResult(() =>
        api.detail(id, AbortSignal.any([signal, controller.signal]))));
      if (current !== epoch) return;
      if (result.ok) {
        thread.value = result.value;
        if (discardRetry) {
          submission.discardRetry();
          message.value = "История обновлена. Проверьте сохранённые вопросы перед новой отправкой; сервер мог продолжить предыдущий запрос.";
        }
        return true;
      }
      else message.value = result.error.status === 404 ? "Диалог недоступен."
        : "Не удалось загрузить диалог. Повторите обновление.";
    } catch (error) { if (!(error instanceof ApiError)) throw error; }
    finally { if (current === epoch) loading.value = false; }
  }
  async function sendQuestion(input: string) {
    if (sending.value || loading.value || retryRequest.value !== null) return;
    if (currentId !== null && thread.value === null) {
      message.value = "Диалог недоступен. Обновите его или начните новый.";
      return;
    }
    if (!input.trim() || input.includes("\0") || new TextEncoder().encode(input).length > 16384) {
      message.value = "Введите вопрос не длиннее 16 КиБ, без нулевых символов.";
      return;
    }
    if (thread.value === null) {
      const current = epoch;
      loading.value = true;
      try {
        const result = await session.runProtected((signal, token) => protectedResult(() =>
          api.create(token, AbortSignal.any([signal, controller.signal]))));
        if (current !== epoch) return;
        if (!result.ok) { message.value = "Не удалось создать диалог. Повторите позже."; return; }
        thread.value = { ...result.value, turns: [] };
        currentId = result.value.id;
      } catch (error) { if (!(error instanceof ApiError)) throw error; }
      finally { if (current === epoch) loading.value = false; }
    }
    if (thread.value === null) return;
    return await submission.transmit(thread.value.id, { request_id: crypto.randomUUID(),
      expected_thread_revision: thread.value.revision, user_input: input });
  }
  async function retry() {
    const saved = retryRequest.value;
    if (saved === null || sending.value || loading.value || currentId !== saved.threadId) return;
    if (thread.value === null) {
      const current = epoch;
      const restored = await loadThread(saved.threadId);
      if (!restored || epoch !== current + 1 || currentId !== saved.threadId
        || retryRequest.value?.body.request_id !== saved.body.request_id) return;
    }
    if (thread.value?.id !== saved.threadId) return;
    return await submission.transmit(saved.threadId, saved.body);
  }
  function cancel() {
    epoch += 1;
    controller.abort();
    controller = new AbortController();
    submission.cancel();
    loading.value = false;
    inspector.clear();
    selectedTurn.value = null;
    message.value = "Ожидание остановлено. Сервер мог продолжить обработку; обновите диалог или повторите тот же запрос.";
  }
  function newThread() {
    resetView();
    currentId = null;
    submission.discardRetry();
  }
  return { thread: readonly(thread), threads: readonly(threads), loading: readonly(loading),
    sending: readonly(sending), message: readonly(message), selectedTurn: readonly(selectedTurn),
    pendingSubmission, completedSubmission, progress,
    retryRequest: readonly(retryRequest), sources, inspector, loadThread, listThreads,
    sendQuestion, retry, selectTurn, cancel, newThread, clear,
    dispose: () => { clear(); unsubscribe(); inspector.dispose(); } };
}
