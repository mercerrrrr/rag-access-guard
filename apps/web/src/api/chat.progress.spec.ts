import { afterEach, expect, it, vi } from "vitest";
import { chatApi } from "@/api/chat";
import { reply, thread } from "@/test/chat";

afterEach(() => vi.unstubAllGlobals());
const status = { request_id: reply.turn.request_id, state: "running", stage: "generation", elapsed_seconds: 2 };

it("reads a bound status without manually setting Origin", async () => {
  const requests: Request[] = [];
  vi.stubGlobal("fetch", (request: Request) => { requests.push(request); return Promise.resolve(Response.json(status)); });
  await expect(chatApi.status(thread.id, reply.turn.request_id, new AbortController().signal)).resolves.toEqual(status);
  expect(requests[0]?.url).toContain(`/requests/${reply.turn.request_id}/status`);
  expect(requests[0]?.headers.has("Origin")).toBe(false);
});

it.each([
  { ...status, answer: "SECRET" }, { ...status, user_input: "SECRET" },
  { ...status, sources: [] }, { ...status, turn_id: reply.turn.id },
  { ...status, state: "terminal" }, { ...status, state: "absent", stage: null },
  { ...status, state: "expired", stage: null }, { ...status, elapsed_seconds: -1 },
  { ...status, elapsed_seconds: 0.5 }, { ...status, stage: null },
  { ...status, request_id: thread.id }, { ...status, stage: "thinking" },
])("rejects unsafe or impossible status %j", async (body) => {
  vi.stubGlobal("fetch", () => Promise.resolve(Response.json(body)));
  await expect(chatApi.status(thread.id, reply.turn.request_id, new AbortController().signal)).rejects.toMatchObject({ status: 0 });
});

it.each([["invalid", reply.turn.request_id], [thread.id, "invalid"]])("rejects invalid path ids before transport", async (id, requestId) => {
  const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
  await expect(async () => chatApi.status(id, requestId, new AbortController().signal)).rejects.toMatchObject({ status: 0 });
  expect(fetch).not.toHaveBeenCalled();
});
