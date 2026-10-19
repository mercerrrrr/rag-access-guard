import { afterEach, expect, it, vi } from "vitest";
import { ApiError } from "@/api/errors";
import type { MessageRequest, ThreadDetail } from "@/api/types";
import { createChatState } from "@/composables/useChat";
import { createSessionState } from "@/composables/useSession";
import { chatApi, reply, thread } from "@/test/chat";
import { deferred, sessionApi } from "@/test/session";

afterEach(() => vi.useRealTimers());

it.each([
  { firstQuestion: false, replayed: false, failure: 500 },
  { firstQuestion: true, replayed: true, failure: 0 },
])("manually retries the exact payload after failed canonical recovery $firstQuestion/$failure", async scenario => {
  vi.useFakeTimers(); const session = createSessionState(sessionApi()); await session.refresh();
  const calls: MessageRequest[] = []; let reads = 0; let creations = 0;
  const prior: ThreadDetail = { ...thread, revision: 3, turns: [reply.turn] };
  function canonical(): ThreadDetail {
    return { ...thread, revision: scenario.firstQuestion ? 1 : 4,
      turns: [...(scenario.firstQuestion ? [] : prior.turns), { ...reply.turn,
        id: "00000000-0000-4000-8000-000000000021",
        request_id: calls[0]?.request_id ?? "", user_input: "Новый вопрос  " }] };
  }
  const state = createChatState(chatApi({
    create: () => { creations += 1; return Promise.resolve(thread); },
    detail: () => {
      reads += 1;
      if (reads === (scenario.firstQuestion ? 1 : 2)) return Promise.reject(new ApiError(scenario.failure));
      return Promise.resolve(!scenario.firstQuestion && reads === 1 ? prior : canonical());
    },
    status: (_id, requestId) => Promise.resolve({ request_id: requestId, state: "terminal", stage: null, elapsed_seconds: null }),
    send: (_id, body) => {
      calls.push(body);
      return calls.length === 1 ? Promise.reject(new ApiError(0)) : Promise.resolve({ ...reply,
        thread_revision: scenario.firstQuestion ? 1 : 4, replayed: scenario.replayed,
        turn: { ...reply.turn, id: "00000000-0000-4000-8000-000000000021",
          request_id: body.request_id, user_input: body.user_input } });
    },
  }), session);
  if (!scenario.firstQuestion) await state.loadThread(thread.id);
  await state.sendQuestion("Новый вопрос  "); await vi.advanceTimersByTimeAsync(1000);
  const saved = state.retryRequest.value;
  expect(state.thread.value).toBeNull(); expect(state.inspector.content.value).toBeNull();
  expect(state.sending.value).toBe(false); expect(calls).toHaveLength(1);
  const result = await state.retry();
  expect(calls).toHaveLength(2); expect(calls[1]).toEqual(calls[0]);
  expect(calls[1]?.expected_thread_revision).toBe(scenario.firstQuestion ? 0 : 3);
  expect(calls[1]?.request_id).toBe(saved?.body.request_id); expect(calls[1]?.user_input).toBe("Новый вопрос  ");
  expect(result).toEqual({ threadId: thread.id, userInput: "Новый вопрос  " });
  expect(state.thread.value?.turns).toHaveLength(scenario.firstQuestion ? 1 : 2);
  expect(state.pendingSubmission.value).toBeNull(); expect(state.retryRequest.value).toBeNull();
  expect(creations).toBe(scenario.firstQuestion ? 1 : 0); state.dispose();
});

it.each(["thread", "session"] as const)("discards a late manual recovery after %s reset", async reset => {
  vi.useFakeTimers(); const session = createSessionState(sessionApi()); await session.refresh();
  const late = deferred<ThreadDetail>(); let reads = 0; let sends = 0; let signal: AbortSignal | undefined;
  const state = createChatState(chatApi({
    detail: (_id, currentSignal) => {
      reads += 1;
      if (reads === 1) return Promise.resolve(thread);
      if (reads === 2) return Promise.reject(new ApiError(500));
      signal = currentSignal; return late.promise;
    },
    status: (_id, requestId) => Promise.resolve({ request_id: requestId, state: "terminal", stage: null, elapsed_seconds: null }),
    send: () => { sends += 1; return Promise.reject(new ApiError(0)); },
  }), session);
  await state.loadThread(thread.id); await state.sendQuestion("Вопрос"); await vi.advanceTimersByTimeAsync(1000);
  const retrying = state.retry();
  expect(reads).toBe(3); expect(state.loading.value).toBe(true);
  if (reset === "thread") state.newThread(); else session.clearProtectedState();
  expect(signal?.aborted).toBe(true);
  late.resolve({ ...thread, revision: 1, turns: [reply.turn] }); await retrying;
  expect(sends).toBe(1); expect(state.thread.value).toBeNull(); expect(state.retryRequest.value).toBeNull();
  expect(state.completedSubmission.value).toBeNull(); expect(state.inspector.content.value).toBeNull(); state.dispose();
});

it("reports another failed manual recovery without POST or cached protected content", async () => {
  vi.useFakeTimers(); const session = createSessionState(sessionApi()); await session.refresh();
  let reads = 0; let sends = 0;
  const state = createChatState(chatApi({
    detail: () => ++reads === 1 ? Promise.resolve({ ...thread, revision: 1, turns: [reply.turn] }) : Promise.reject(new ApiError(500)),
    status: (_id, requestId) => Promise.resolve({ request_id: requestId, state: "terminal", stage: null, elapsed_seconds: null }),
    send: () => { sends += 1; return Promise.reject(new ApiError(0)); },
  }), session);
  await state.loadThread(thread.id); await state.sendQuestion("Вопрос"); await vi.advanceTimersByTimeAsync(1000);
  const saved = state.retryRequest.value; await state.retry();
  expect(reads).toBe(3); expect(sends).toBe(1); expect(state.thread.value).toBeNull();
  expect(state.retryRequest.value).toEqual(saved); expect(state.loading.value).toBe(false);
  expect(state.message.value).not.toBe(""); expect(state.sources.value).toEqual([]); state.dispose();
});
