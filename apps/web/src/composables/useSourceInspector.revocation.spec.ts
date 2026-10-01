import { expect, it } from "vitest";
import type { SourceContent, ThreadDetail } from "@/api/types";
import { createChatState } from "@/composables/useChat";
import { createSessionState } from "@/composables/useSession";
import { chatApi, reply, source, thread } from "@/test/chat";
import { deferred, sessionApi } from "@/test/session";

it("a_late_source_read_cannot_reopen_an_unavailable_answer", async () => {
  const pending = deferred<SourceContent>();
  let snapshot: ThreadDetail = { ...thread, revision: 1, turns: [reply.turn] };
  const session = createSessionState(sessionApi());
  await session.refresh();
  const state = createChatState(chatApi({ detail: () => Promise.resolve(snapshot),
    source: () => pending.promise }), session);
  await state.loadThread(thread.id);
  state.selectTurn(reply.turn.id);
  const read = state.inspector.open(source);
  snapshot = { ...thread, revision: 1, turns: [{ ...reply.turn, state: "unavailable",
    answer: null, sources: [], message: "Ответ недоступен: права на один из источников изменились." }] };
  await state.loadThread(thread.id);

  pending.resolve({ ...source, text: "SYNTHETIC_SECRET_53" });
  await read;

  expect(state.inspector.content.value).toBeNull();
  expect(state.inspector.selected.value).toBeNull();
  expect(state.inspector.busy.value).toBe(false);
  expect(state.sources.value).toEqual([]);
  expect(state.thread.value?.turns[0]?.state).toBe("unavailable");
  state.dispose();
});
