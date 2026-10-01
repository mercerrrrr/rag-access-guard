import { expect, it } from "vitest";
import { ApiError } from "@/api/errors";
import type { MessageRequest, MessageResponse, ThreadDetail } from "@/api/types";
import { createChatState } from "@/composables/useChat";
import { createSessionState } from "@/composables/useSession";
import { chatApi, reply, thread } from "@/test/chat";
import { deferred, sessionApi } from "@/test/session";

it("late_thread_does_not_replace_new_thread", async () => {
  const session = createSessionState(sessionApi());
  await session.refresh();
  const pending = deferred<ThreadDetail>();
  const nextId = "00000000-0000-4000-8000-000000000011";
  const state = createChatState(chatApi({ detail: (id) => id === thread.id
    ? pending.promise : Promise.resolve({ ...thread, id }) }), session);
  const first = state.loadThread(thread.id);
  await state.loadThread(nextId);
  pending.resolve(thread);
  await first;
  expect(state.thread.value?.id).toBe(nextId);
});

it("late_turn_does_not_enter_new_thread", async () => {
  const session = createSessionState(sessionApi());
  await session.refresh();
  const pending = deferred<MessageResponse>();
  const calls: MessageRequest[] = [];
  const state = createChatState(chatApi({ send: (_id, body) => {
    calls.push(body); return pending.promise;
  } }), session);
  await state.loadThread(thread.id);
  const first = state.sendQuestion("Вопрос");
  expect(calls).toHaveLength(1);
  await state.loadThread("00000000-0000-4000-8000-000000000011");
  pending.resolve(reply);
  await first;
  expect(state.thread.value?.turns).toEqual([]);
});

it("double_submit_uses_one_request_id", async () => {
  const session = createSessionState(sessionApi());
  await session.refresh();
  const pending = deferred<MessageResponse>();
  const calls: MessageRequest[] = [];
  const state = createChatState(chatApi({ send: (_id, body) => {
    calls.push(body); return pending.promise;
  } }), session);
  await state.loadThread(thread.id);
  const first = state.sendQuestion("Вопрос");
  const second = state.sendQuestion("Вопрос");
  expect(calls).toHaveLength(1);
  expect(calls[0]?.request_id).toMatch(/^[\da-f-]{36}$/);
  pending.resolve(reply);
  await Promise.all([first, second]);
});

it("retry_after_refetch_keeps_original_payload", async () => {
  const session = createSessionState(sessionApi());
  await session.refresh();
  const calls: MessageRequest[] = [];
  let revision = 0;
  const state = createChatState(chatApi({
    detail: () => Promise.resolve({ ...thread, revision }),
    send: (_id, body) => {
      calls.push(body);
      return calls.length === 1 ? Promise.reject(new ApiError(0)) : Promise.resolve(reply);
    },
  }), session);
  await state.loadThread(thread.id);
  await state.sendQuestion("Вопрос без обрезки  ");
  revision = 1;
  await state.loadThread(thread.id);
  await state.retry();
  expect(calls).toHaveLength(2);
  expect(calls[1]).toEqual(calls[0]);
  expect(calls[1]?.expected_thread_revision).toBe(0);
  expect(calls[1]?.user_input).toBe("Вопрос без обрезки  ");
});

it("late_conflict_refresh_does_not_write_into_new_thread", async () => {
  const session = createSessionState(sessionApi());
  await session.refresh();
  const pending = deferred<ThreadDetail>();
  const entered = deferred<boolean>();
  let reads = 0;
  const nextId = "00000000-0000-4000-8000-000000000011";
  const state = createChatState(chatApi({
    detail: (id) => {
      if (id === thread.id && ++reads > 1) { entered.resolve(true); return pending.promise; }
      return Promise.resolve({ ...thread, id });
    },
    send: () => Promise.reject(new ApiError(409)),
  }), session);
  await state.loadThread(thread.id);
  const sending = state.sendQuestion("Вопрос");
  await entered.promise;
  await state.loadThread(nextId);
  pending.resolve(thread);
  await sending;
  expect(state.thread.value?.id).toBe(nextId);
  expect(state.message.value).toBe("");
});

it("unavailable_thread_cannot_silently_create_another", async () => {
  const session = createSessionState(sessionApi());
  await session.refresh();
  let creations = 0;
  const state = createChatState(chatApi({
    detail: () => Promise.reject(new ApiError(404)),
    create: () => { creations += 1; return Promise.resolve(thread); },
  }), session);
  await state.loadThread(thread.id);
  await state.sendQuestion("Вопрос");
  expect(creations).toBe(0);
});

it("cancel_stops_local_wait_but_preserves_exact_retry", async () => {
  const session = createSessionState(sessionApi());
  await session.refresh();
  const pending = deferred<MessageResponse>();
  const state = createChatState(chatApi({ send: () => pending.promise }), session);
  await state.loadThread(thread.id);
  const sending = state.sendQuestion("Вопрос");
  const request = state.retryRequest.value;
  state.cancel();
  pending.resolve(reply);
  await sending;
  expect(state.sending.value).toBe(false);
  expect(state.thread.value?.turns).toEqual([]);
  expect(state.retryRequest.value).toEqual(request);
  expect(state.message.value).toContain("Сервер мог продолжить");
});

it("session_clear_removes_thread_sources_and_retry", async () => {
  const session = createSessionState(sessionApi());
  await session.refresh();
  const state = createChatState(chatApi(), session);
  await state.loadThread(thread.id);
  await state.sendQuestion("Вопрос");
  expect(state.sources.value).toHaveLength(1);
  session.clearProtectedState();
  expect(state.thread.value).toBeNull();
  expect(state.sources.value).toEqual([]);
  expect(state.retryRequest.value).toBeNull();
});

it("explicit_conflict_reconciliation_allows_editing_without_resending", async () => {
  const session = createSessionState(sessionApi());
  await session.refresh();
  const calls: MessageRequest[] = [];
  let revision = 0;
  const state = createChatState(chatApi({
    detail: () => Promise.resolve({ ...thread, revision }),
    send: (_id, body) => {
      calls.push(body);
      revision = 1;
      return Promise.reject(new ApiError(409));
    },
  }), session);
  await state.loadThread(thread.id);
  await state.sendQuestion("Первый вопрос");
  expect(state.retryRequest.value?.body.expected_thread_revision).toBe(0);
  await state.loadThread(thread.id, true);
  expect(state.retryRequest.value).toBeNull();
  expect(calls).toHaveLength(1);
  await state.sendQuestion("Уточнённый вопрос");
  expect(calls).toHaveLength(2);
  expect(calls[1]?.expected_thread_revision).toBe(1);
  expect(calls[1]?.request_id).not.toBe(calls[0]?.request_id);
});

it("replay_of_an_earlier_turn_preserves_canonical_history_order", async () => {
  const session = createSessionState(sessionApi());
  await session.refresh();
  let detail = thread;
  let firstRequest: MessageRequest | undefined;
  const laterTurn = { ...reply.turn, id: "00000000-0000-4000-8000-000000000021",
    request_id: "00000000-0000-4000-8000-000000000031", user_input: "Вопрос Б" };
  const state = createChatState(chatApi({ detail: () => Promise.resolve(detail), send: (_id, body) => {
    if (!firstRequest) { firstRequest = body; return Promise.reject(new ApiError(0)); }
    return Promise.resolve({ ...reply, replayed: true, thread_revision: 2,
      turn: { ...reply.turn, request_id: body.request_id, user_input: body.user_input } });
  } }), session);
  await state.loadThread(thread.id);
  await state.sendQuestion("Вопрос А");
  detail = { ...thread, revision: 2, turns: [
    { ...reply.turn, request_id: firstRequest?.request_id ?? "", user_input: "Вопрос А" }, laterTurn,
  ] };
  await state.loadThread(thread.id);
  await state.retry();
  expect(state.thread.value?.turns.map(turn => turn.user_input)).toEqual(["Вопрос А", "Вопрос Б"]);
});
