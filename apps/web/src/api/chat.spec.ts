import { afterEach, expect, it, vi } from "vitest";
import { chatApi } from "@/api/chat";
import { reply, thread } from "@/test/chat";
import { sourceSchema } from "@/api/types";

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

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
