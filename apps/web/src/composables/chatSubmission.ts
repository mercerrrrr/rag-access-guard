import { readonly, ref } from "vue";
import { ApiError, errorMessage } from "@/api/errors";
import { protectedResult } from "@/api/protectedResult";
import type { ChatApi, MessageRequest, ThreadDetail } from "@/api/types";
import { createRequestProgress } from "@/composables/useRequestProgress";
import type { SessionState } from "@/composables/useSession";

type Submission = { readonly threadId: string; readonly body: MessageRequest };
type Completion = { readonly threadId: string; readonly userInput: string };
type ChatAccess = {
  readonly getThread: () => ThreadDetail | null;
  readonly getId: () => string | null;
  readonly setThread: (thread: ThreadDetail | null) => void;
  readonly setMessage: (message: string) => void;
  readonly clearSources: () => void;
  readonly missing: () => void;
  readonly refresh: (id: string) => Promise<boolean | undefined>;
  readonly selectTurn: (id: string) => void;
  readonly listThreads: () => Promise<void>;
};

export function createChatSubmission(api: ChatApi, session: SessionState, view: ChatAccess) {
  const sending = ref(false);
  const retryRequest = ref<Submission | null>(null);
  const pendingSubmission = ref<{ readonly threadId: string; readonly request_id: string; readonly user_input: string } | null>(null);
  const completedSubmission = ref<Completion | null>(null);
  let epoch = 0;
  let controller = new AbortController();
  let postLost = false;
  let recovering = false;
  const progress = createRequestProgress(api, session, recover, view.missing, () => { stopWaiting("expired"); });

  function reset(clearRetry = false) {
    epoch += 1; controller.abort(); controller = new AbortController();
    progress.clear(); sending.value = false; pendingSubmission.value = null;
    completedSubmission.value = null; postLost = false; recovering = false;
    if (clearRetry) retryRequest.value = null;
  }
  function complete(saved: Submission) {
    pendingSubmission.value = null; retryRequest.value = null; progress.clear();
    completedSubmission.value = { threadId: saved.threadId, userInput: saved.body.user_input };
    void view.listThreads();
    return completedSubmission.value;
  }
  async function recover() {
    const saved = retryRequest.value;
    if (!postLost || recovering || saved === null || progress.status.value?.state !== "terminal") return;
    recovering = true;
    const current = epoch;
    view.clearSources(); view.setThread(null);
    try {
      const result = await session.runProtected(signal => protectedResult(() =>
        api.detail(saved.threadId, AbortSignal.any([signal, controller.signal]))));
      if (current !== epoch) return;
      if (result.ok) {
        view.setThread(result.value);
        const turn = result.value.turns.find(turn => turn.request_id === saved.body.request_id);
        if (turn && turn.state !== "pending") {
          complete(saved); view.selectTurn(turn.id); view.setMessage("");
        }
      } else if (result.error.status === 404) view.missing();
      else view.setMessage("Не удалось загрузить диалог. Повторите обновление.");
    } catch (error) { if (!(error instanceof ApiError)) throw error; }
  }
  async function transmit(threadId: string, body: MessageRequest) {
    const current = epoch;
    const saved = { threadId, body };
    sending.value = true; postLost = false; recovering = false;
    view.setMessage(""); view.clearSources();
    retryRequest.value = saved;
    pendingSubmission.value = { threadId, request_id: body.request_id, user_input: body.user_input };
    progress.start(threadId, body.request_id);
    try {
      const result = await session.runProtected((signal, token) => protectedResult(() =>
        api.send(threadId, body, token, AbortSignal.any([signal, controller.signal]))));
      if (current !== epoch || view.getId() !== threadId) return;
      if (result.ok) {
        const response = result.value;
        if (response.replayed) {
          const refreshed = await view.refresh(threadId);
          if (!refreshed || epoch !== current + 1 || view.getId() !== threadId) return;
        } else {
          const thread = view.getThread();
          if (!thread) return;
          view.setThread({ ...thread, revision: response.thread_revision,
            turns: [...thread.turns.filter(turn => turn.request_id !== body.request_id), response.turn] });
        }
        const completion = complete(saved); view.selectTurn(response.turn.id);
        return completion;
      }
      if (result.error.status === 409) {
        await view.refresh(threadId);
        if (epoch === current + 1 && view.getId() === threadId) {
          view.setMessage("Запрос уже выполняется или диалог изменился. Данные обновлены; автоматического повтора нет.");
        }
      } else if (result.error.status === 422 || result.error.code === "inference_busy"
        || result.error.code === "inference_unavailable") {
        pendingSubmission.value = null; retryRequest.value = null; progress.clear();
        view.setMessage(result.error.status === 422 && result.error.code !== "query_too_long"
          ? "Вопрос не принят. Проверьте текст и отправьте его снова." : errorMessage(result.error));
      } else if (result.error.status === 404) view.missing();
      else {
        postLost = true;
        view.setMessage("Ответ не получен. Обновите диалог или повторите тот же запрос без создания нового вопроса.");
        await recover();
      }
    } catch (error) { if (!(error instanceof ApiError)) throw error; }
    finally { if (current === epoch) sending.value = false; }
  }
  function stopWaiting(phase: "stopped" | "expired") {
    epoch += 1; controller.abort(); controller = new AbortController();
    sending.value = false; completedSubmission.value = null; postLost = false; recovering = false;
    progress.stop(phase);
  }
  function cancel() { stopWaiting("stopped"); }
  function discardRetry() { retryRequest.value = null; }
  return { sending: readonly(sending), retryRequest: readonly(retryRequest),
    pendingSubmission: readonly(pendingSubmission), completedSubmission: readonly(completedSubmission),
    progress, reset, transmit, cancel, discardRetry };
}
