import { afterEach, expect, it, vi } from "vitest";
import { ApiError } from "@/api/errors";
import type { ChatApi, MessageRequest, MessageResponse, RequestStatus } from "@/api/types";
import { createChatState } from "@/composables/useChat";
import { createSessionState } from "@/composables/useSession";
import { chatApi, reply, thread } from "@/test/chat";
import { deferred, sessionApi } from "@/test/session";

afterEach(() => vi.useRealTimers());
async function setup(overrides: Partial<ChatApi> = {}) {
  const session = createSessionState(sessionApi()); await session.refresh();
  const state = createChatState(chatApi(overrides), session); await state.loadThread(thread.id);
  return { state, session };
}
it("shows a local question while POST is unresolved and reconciles it once", async () => {
  const pending = deferred<MessageResponse>();
  let body: MessageRequest | undefined;
  const { state } = await setup({ send: (_id, value) => { body = value; return pending.promise; } });
  const sending = state.sendQuestion("<img src=x onerror=alert(1)>");
  expect(state.pendingSubmission.value?.user_input).toBe("<img src=x onerror=alert(1)>");
  expect(state.thread.value?.turns).toEqual([]);
  pending.resolve({ ...reply, turn: { ...reply.turn, request_id: body?.request_id ?? "", user_input: body?.user_input ?? "" } });
  await sending;
  expect(state.pendingSubmission.value).toBeNull();
  expect(state.thread.value?.turns).toHaveLength(1);
  state.dispose();
});
it.each(["clear", "newThread", "dispose"] as const)("discards pending and late POST after %s", async (action) => {
  const pending = deferred<MessageResponse>();
  const { state } = await setup({ send: () => pending.promise });
  const sending = state.sendQuestion("Вопрос"); state[action](); pending.resolve(reply); await sending;
  expect(state.pendingSubmission.value).toBeNull(); expect(state.thread.value).toBeNull();
  state.dispose();
});
it.each([new ApiError(422, null, "query_too_long"), new ApiError(429, 1, "inference_busy"),
  new ApiError(503, null, "inference_unavailable")])("clears pending and retry for a pre-admission rejection %j", async error => {
  const { state } = await setup({ send: () => Promise.reject(error) });
  const result = await state.sendQuestion("Вопрос  ");
  expect(result).toBeUndefined(); expect(state.pendingSubmission.value).toBeNull();
  expect(state.retryRequest.value).toBeNull(); state.dispose();
});

it("polls sequentially and permits a real stage to return to retrieval", async () => {
  vi.useFakeTimers(); const post = deferred<MessageResponse>(); const poll = deferred<RequestStatus>();
  let reads = 0;
  const { state } = await setup({ send: () => post.promise, status: (_id, requestId) => {
    reads += 1;
    return reads === 1 ? poll.promise : Promise.resolve({ request_id: requestId,
      state: "running", stage: "retrieval", elapsed_seconds: 8 });
  } });
  void state.sendQuestion("Вопрос"); await vi.advanceTimersByTimeAsync(5000);
  expect(reads).toBe(1);
  poll.resolve({ request_id: state.pendingSubmission.value?.request_id ?? "", state: "running", stage: "generation", elapsed_seconds: 5 });
  await vi.advanceTimersByTimeAsync(0); expect(state.progress.phase.value).toBe("generation");
  await vi.advanceTimersByTimeAsync(1000); expect(state.progress.phase.value).toBe("retrieval");
  state.dispose(); post.resolve(reply);
});

it("keeps sending on absent status until the original POST completes", async () => {
  vi.useFakeTimers(); const post = deferred<MessageResponse>();
  const { state } = await setup({ send: () => post.promise });
  const sending = state.sendQuestion("Вопрос"); await vi.advanceTimersByTimeAsync(2000);
  expect(state.progress.phase.value).toBe("sending"); expect(state.sending.value).toBe(true);
  post.resolve(reply); await sending; state.dispose();
});

it("terminal polling stops but does not invalidate a legitimate original POST", async () => {
  vi.useFakeTimers(); const post = deferred<MessageResponse>(); let reads = 0;
  const { state } = await setup({ send: () => post.promise, status: (_id, requestId) => {
    reads += 1; return Promise.resolve({ request_id: requestId, state: "terminal", stage: null, elapsed_seconds: null });
  } });
  const sending = state.sendQuestion("Вопрос"); await vi.advanceTimersByTimeAsync(5000);
  expect(reads).toBe(1); expect(state.sending.value).toBe(true);
  post.resolve(reply); expect(await sending).toEqual({ threadId: thread.id, userInput: "Вопрос" });
  expect(state.thread.value?.turns).toHaveLength(1); expect(state.retryRequest.value).toBeNull(); state.dispose();
});

it.each(["before", "after"] as const)("recovers canonical history exactly once when POST is lost %s terminal status", async order => {
  vi.useFakeTimers(); const post = deferred<ApiError>(); let reads = 0; let sends = 0;
  let body: MessageRequest | undefined;
  const { state } = await setup({ send: (_id, value) => { body = value; sends += 1; return post.promise.then(error => { throw error; }); },
    status: (_id, requestId) => Promise.resolve({ request_id: requestId, state: "terminal", stage: null, elapsed_seconds: null }),
    detail: () => { reads += 1; return Promise.resolve(reads === 1 ? thread : { ...thread, revision: 1,
      turns: [{ ...reply.turn, request_id: body?.request_id ?? "", user_input: "Вопрос" }] }); },
  });
  const sending = state.sendQuestion("Вопрос");
  if (order === "before") { post.resolve(new ApiError(0)); await sending; }
  await vi.advanceTimersByTimeAsync(1000);
  if (order === "after") { post.resolve(new ApiError(500)); await sending; }
  await vi.advanceTimersByTimeAsync(5000);
  expect(reads).toBe(2); expect(sends).toBe(1); expect(state.thread.value?.turns).toHaveLength(1);
  expect(state.pendingSubmission.value).toBeNull(); expect(state.retryRequest.value).toBeNull();
  expect(state.completedSubmission.value).toEqual({ threadId: thread.id, userInput: "Вопрос" }); state.dispose();
});

it("ordinary polling failure preserves session and lets POST complete", async () => {
  vi.useFakeTimers(); const post = deferred<MessageResponse>(); let reads = 0;
  const { state, session } = await setup({ send: () => post.promise, status: () => {
    reads += 1; return Promise.reject(new ApiError(500));
  } });
  const sending = state.sendQuestion("Вопрос"); await vi.advanceTimersByTimeAsync(4000);
  expect(reads).toBe(1); expect(session.status.value).toBe("authenticated");
  expect(state.thread.value?.id).toBe(thread.id); expect(state.progress.phase.value).toBe("unavailable");
  post.resolve(reply); await sending; expect(state.thread.value?.turns).toHaveLength(1); state.dispose();
});

it.each([401, 403, 404])("clears protected content and pending on status %s", async status => {
  vi.useFakeTimers(); const post = deferred<MessageResponse>();
  const { state, session } = await setup({ send: () => post.promise, status: () => Promise.reject(new ApiError(status)) });
  const sending = state.sendQuestion("Вопрос"); await vi.advanceTimersByTimeAsync(1000);
  expect(state.thread.value).toBeNull(); expect(state.pendingSubmission.value).toBeNull();
  if (status === 401) expect(session.status.value).toBe("anonymous");
  post.resolve(reply); await sending; expect(state.thread.value).toBeNull(); state.dispose();
});

it("expired stops polling and retains the exact retry payload", async () => {
  vi.useFakeTimers(); const post = deferred<ApiError>(); let reads = 0;
  const { state } = await setup({ send: () => post.promise.then(error => { throw error; }), status: (_id, requestId) => {
    reads += 1; return Promise.resolve({ request_id: requestId, state: "expired", stage: null, elapsed_seconds: null });
  } });
  const sending = state.sendQuestion("Вопрос  "); const retry = state.retryRequest.value;
  post.resolve(new ApiError(0)); await sending; await vi.advanceTimersByTimeAsync(5000);
  expect(reads).toBe(1); expect(state.progress.phase.value).toBe("expired");
  expect(state.retryRequest.value).toEqual(retry); state.dispose();
});

it("cancel retains elapsed waiting time and aborts both local requests", async () => {
  vi.useFakeTimers(); const post = deferred<MessageResponse>(); const poll = deferred<RequestStatus>();
  let postSignal: AbortSignal | undefined; let pollSignal: AbortSignal | undefined;
  const { state } = await setup({ send: (_id, _body, _token, signal) => { postSignal = signal; return post.promise; },
    status: (_id, _requestId, signal) => { pollSignal = signal; return poll.promise; } });
  const sending = state.sendQuestion("Вопрос"); await vi.advanceTimersByTimeAsync(3000);
  state.cancel();
  expect(postSignal?.aborted).toBe(true); expect(pollSignal?.aborted).toBe(true);
  expect(state.progress.phase.value).toBe("stopped"); expect(state.progress.elapsed.value).toBe(3);
  expect(vi.getTimerCount()).toBe(0);
  poll.resolve({ request_id: "", state: "running", stage: "generation", elapsed_seconds: 4 });
  post.resolve(reply); await sending; await vi.advanceTimersByTimeAsync(1000);
  expect(state.thread.value?.turns).toEqual([]); expect(state.progress.elapsed.value).toBe(3); state.dispose();
});

it("expired releases an unresolved POST wait and isolates its late result from exact retry", async () => {
  vi.useFakeTimers(); const original = deferred<MessageResponse>(); const repeated = deferred<MessageResponse>();
  const calls: MessageRequest[] = []; let originalSignal: AbortSignal | undefined;
  const { state } = await setup({ send: (_id, body, _token, signal) => {
    calls.push(body);
    if (calls.length === 1) { originalSignal = signal; return original.promise; }
    return repeated.promise;
  }, status: (_id, requestId) => Promise.resolve({ request_id: requestId, state: "expired", stage: null, elapsed_seconds: null }) });
  const first = state.sendQuestion("Вопрос  "); const retry = state.retryRequest.value;
  await vi.advanceTimersByTimeAsync(1000);
  expect(state.sending.value).toBe(false); expect(originalSignal?.aborted).toBe(true);
  expect(state.progress.phase.value).toBe("expired"); expect(state.progress.elapsed.value).toBe(1);
  expect(state.pendingSubmission.value?.user_input).toBe("Вопрос  "); expect(state.retryRequest.value).toEqual(retry);
  expect(vi.getTimerCount()).toBe(0);
  const second = state.retry(); expect(calls).toHaveLength(2); expect(calls[1]).toEqual(calls[0]);
  original.resolve({ ...reply, thread_revision: 9, turn: { ...reply.turn, state: "available", answer: "LATE_ORIGINAL",
    message: null, request_id: retry?.body.request_id ?? "", user_input: "Вопрос  " } }); await first;
  expect(state.sending.value).toBe(true); expect(state.thread.value?.turns).toEqual([]);
  expect(state.retryRequest.value).toEqual(retry); expect(state.completedSubmission.value).toBeNull();
  repeated.resolve({ ...reply, turn: { ...reply.turn, request_id: retry?.body.request_id ?? "", user_input: "Вопрос  " } }); await second;
  expect(state.thread.value?.turns).toHaveLength(1); expect(state.thread.value?.revision).toBe(1);
  expect(state.thread.value?.turns[0]?.answer).toBe(reply.turn.answer); state.dispose();
});
