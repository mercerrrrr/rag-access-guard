import { expect, it } from "vitest";
import { ApiError } from "@/api/errors";
import type { SourceContent } from "@/api/types";
import { createSourceInspector } from "@/composables/useSourceInspector";
import { createSessionState } from "@/composables/useSession";
import { chatApi, source } from "@/test/chat";
import { deferred, sessionApi } from "@/test/session";

it("source_404_clears_inspector", async () => {
  let denied = false;
  const session = createSessionState(sessionApi());
  await session.refresh();
  const inspector = createSourceInspector(chatApi({ source: () => denied
    ? Promise.reject(new ApiError(404)) : Promise.resolve({ ...source, text: "private" }) }), session);
  await inspector.open(source);
  expect(inspector.content.value?.text).toBe("private");
  denied = true;
  await inspector.open(source);
  expect(inspector.content.value).toBeNull();
  expect(inspector.selected.value).toBeNull();
});

it("late_source_after_clear_cannot_restore_content", async () => {
  const session = createSessionState(sessionApi());
  await session.refresh();
  const pending = deferred<SourceContent>();
  const inspector = createSourceInspector(chatApi({ source: () => pending.promise }), session);
  const request = inspector.open(source);
  inspector.clear();
  pending.resolve({ ...source, text: "private" });
  await request;
  expect(inspector.content.value).toBeNull();
});

it("source_content_must_match_all_three_refs", async () => {
  const session = createSessionState(sessionApi());
  await session.refresh();
  const inspector = createSourceInspector(chatApi({ source: () => Promise.resolve({
    ...source, chunk_id: "00000000-0000-4000-8000-000000000004", text: "wrong chunk",
  }) }), session);
  await inspector.open(source);
  expect(inspector.content.value).toBeNull();
});
