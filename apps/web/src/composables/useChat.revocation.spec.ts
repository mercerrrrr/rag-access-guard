import { expect, it } from "vitest";
import { ApiError } from "@/api/errors";
import type { ChatApi, ThreadDetail } from "@/api/types";
import { createChatState } from "@/composables/useChat";
import { createSessionState } from "@/composables/useSession";
import { chatApi, reply, source, thread } from "@/test/chat";
import { deferred, sessionApi } from "@/test/session";

function availableThread(): ThreadDetail {
  return { ...thread, revision: 1, turns: [{ ...reply.turn, state: "available",
    answer: "SYNTHETIC_SECRET_53", sources: [{ ...source, title: "Закрытый учебный источник" }], message: null }] };
}
function unavailableThread(): ThreadDetail {
  return { ...thread, revision: 1, turns: [{ ...reply.turn, state: "unavailable", answer: null,
    sources: [], message: "Ответ недоступен: права на один из источников изменились." }] };
}
async function setup(overrides: Partial<ChatApi>) {
  const session = createSessionState(sessionApi());
  await session.refresh();
  return createChatState(chatApi(overrides), session);
}

it("revoked_snapshot_removes_body_and_source_title", async () => {
  let snapshot = availableThread();
  const state = await setup({ detail: () => Promise.resolve(snapshot) });
  await state.loadThread(thread.id);
  expect(JSON.stringify(state.thread.value)).toContain("SYNTHETIC_SECRET_53");
  snapshot = unavailableThread();

  await state.loadThread(thread.id);

  const encoded = JSON.stringify(state.thread.value);
  expect(encoded).not.toContain("SYNTHETIC_SECRET_53");
  expect(encoded).not.toContain("Закрытый учебный источник");
  expect(encoded).not.toContain(source.chunk_id);
  expect(state.thread.value?.turns[0]?.answer).toBeNull();
  expect(state.thread.value?.turns[0]?.sources).toEqual([]);
  expect(state.thread.value?.turns[0]?.user_input).toBe("Вопрос");
  state.dispose();
});

it("source_panel_closes_on_unavailable_turn", async () => {
  let snapshot = availableThread();
  const state = await setup({ detail: () => Promise.resolve(snapshot) });
  await state.loadThread(thread.id);
  state.selectTurn(reply.turn.id);
  await state.inspector.open(source);
  expect(state.inspector.content.value?.text).toBe("Защищённый текст");
  snapshot = unavailableThread();

  await state.loadThread(thread.id);
  state.selectTurn(reply.turn.id);

  expect(state.inspector.content.value).toBeNull();
  expect(state.inspector.selected.value).toBeNull();
  expect(state.sources.value).toEqual([]);
  expect(state.selectedTurn.value).toBeNull();
  state.dispose();
});

it("older_refresh_cannot_restore_secret", async () => {
  const older = deferred<ThreadDetail>();
  let first = true;
  const state = await setup({ detail: () => {
    if (first) { first = false; return older.promise; }
    return Promise.resolve(unavailableThread());
  } });
  const earlierRead = state.loadThread(thread.id);
  await state.loadThread(thread.id);

  older.resolve(availableThread());
  await earlierRead;

  expect(state.thread.value?.turns[0]?.state).toBe("unavailable");
  expect(JSON.stringify(state.thread.value)).not.toContain("SYNTHETIC_SECRET_53");
  expect(state.loading.value).toBe(false);
  state.dispose();
});

it.each([0, 404, 401])("refetch_error_does_not_restore_cached_answer (%s)", async status => {
  const next = deferred<undefined>();
  let refreshed = false;
  const state = await setup({ detail: () => refreshed
    ? next.promise.then(() => { throw new ApiError(status); }) : Promise.resolve(availableThread()) });
  await state.loadThread(thread.id);
  state.selectTurn(reply.turn.id);
  await state.inspector.open(source);
  refreshed = true;

  const request = state.loadThread(thread.id);

  expect(state.thread.value).toBeNull();
  expect(state.inspector.content.value).toBeNull();
  expect(state.sources.value).toEqual([]);
  next.resolve(undefined);
  await request;
  expect(state.thread.value).toBeNull();
  expect(state.inspector.selected.value).toBeNull();
  expect(state.message.value).not.toContain("SYNTHETIC_SECRET_53");
  state.dispose();
});
