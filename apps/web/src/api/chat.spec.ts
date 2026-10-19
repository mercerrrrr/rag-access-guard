import { afterEach, expect, it, vi } from "vitest";
import { chatApi } from "@/api/chat";
import { reply, thread } from "@/test/chat";
import { sourceSchema } from "@/api/types";
import { ApiError, errorMessage } from "@/api/errors";

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

it("accepts_only_the_closed_source_free_help_response", async () => {
  const message = "Я помогаю искать ответы в доступных документах. "
    + "Задайте конкретный вопрос или выберите пример. "
    + "Специальные команды не нужны. Источники ответа можно открыть в разделе «Источники ответа».";
  const help = { ...reply, turn: { ...reply.turn, state: "neutral", answer: null, sources: [], message } };
  vi.stubGlobal("fetch", () => Promise.resolve(Response.json(help)));
  await expect(chatApi.send(thread.id, { request_id: reply.turn.request_id,
    user_input: reply.turn.user_input, expected_thread_revision: 0 }, "csrf", new AbortController().signal))
    .resolves.toEqual(help);
  for (const rejected of [message.replace("в разделе «Источники ответа»", "справа"), "Arbitrary source-free answer"]) {
    vi.stubGlobal("fetch", () => Promise.resolve(Response.json({ ...help,
      turn: { ...help.turn, message: rejected } })));
    await expect(chatApi.send(thread.id, { request_id: reply.turn.request_id,
      user_input: reply.turn.user_input, expected_thread_revision: 0 }, "csrf", new AbortController().signal))
      .rejects.toMatchObject({ status: 0 });
  }
});

it("too_long_query_has_a_closed_user_message", async () => {
  vi.stubGlobal("fetch", () => Promise.resolve(Response.json({ detail: { code: "query_too_long" } }, { status: 422 })));
  const result = await chatApi.send(thread.id, { request_id: reply.turn.request_id,
    user_input: reply.turn.user_input, expected_thread_revision: 0 }, "csrf", new AbortController().signal)
    .catch((error: unknown) => error);
  expect(result).toBeInstanceOf(ApiError);
  if (!(result instanceof ApiError)) throw new Error("Expected typed API failure");
  expect(errorMessage(result)).toBe("Вопрос слишком длинный. Сократите его и попробуйте ещё раз.");
});

it("generation_waits_past_normal_transport_timeout", async () => {
  vi.useFakeTimers();
  vi.stubGlobal("fetch", () => new Promise<Response>((resolve) => {
    setTimeout(() => { resolve(Response.json(reply)); }, 16000);
  }));
  const pending = chatApi.send(thread.id, { request_id: reply.turn.request_id,
    user_input: reply.turn.user_input, expected_thread_revision: 0 }, "csrf", new AbortController().signal);
  const assertion = expect(pending).resolves.toEqual(reply);
  await Promise.all([assertion, vi.advanceTimersByTimeAsync(17000)]);
});

const source = {
  document_id: "00000000-0000-4000-8000-000000000001",
  document_version_id: "00000000-0000-4000-8000-000000000002",
  chunk_id: "00000000-0000-4000-8000-000000000003", title: "Регламент",
  url: "/api/documents/00000000-0000-4000-8000-000000000001/versions/00000000-0000-4000-8000-000000000002/content?chunk_id=00000000-0000-4000-8000-000000000003",
};

it("accepts_a_server_source_with_matching_witness", () => {
  expect(sourceSchema.parse(source)).toEqual(source);
});

it.each([
  "https://foreign.example/source", "//foreign.example/source", "javascript:alert(1)",
  source.url.replace("000003", "000004"), source.url.replace("000002", "000004"),
  source.url.replace("000001", "000004"), `${source.url}&extra=1`, `${source.url}#fragment`,
])("source_ref_mismatch_is_rejected: %s", (url) => {
  expect(sourceSchema.safeParse({ ...source, url }).success).toBe(false);
});

it.each([
  { answer: "SYNTHETIC_SECRET_53", sources: [] },
  { answer: null, sources: [source] },
])("rejects_unavailable_payload_with_protected_fields: %j", async fields => {
  vi.stubGlobal("fetch", () => Promise.resolve(Response.json({ ...thread, turns: [{
    ...reply.turn, state: "unavailable", message: "Ответ недоступен: права на один из источников изменились.",
    ...fields,
  }] })));

  await expect(chatApi.detail(thread.id, new AbortController().signal)).rejects.toMatchObject({ status: 0 });
});
