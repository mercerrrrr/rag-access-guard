import { afterEach, expect, it, vi } from "vitest";
import type { RequestStatus } from "@/api/types";
import { createRequestProgress } from "@/composables/useRequestProgress";
import { createSessionState } from "@/composables/useSession";
import { chatApi, reply, thread } from "@/test/chat";
import { deferred, sessionApi } from "@/test/session";

afterEach(() => vi.useRealTimers());
it("aborts an outstanding status read and rejects its late stage after clear", async () => {
  vi.useFakeTimers(); const session = createSessionState(sessionApi()); await session.refresh();
  const response = deferred<RequestStatus>(); let signal: AbortSignal | undefined;
  const progress = createRequestProgress(chatApi({ status: (_id, _requestId, value) => {
    signal = value; return response.promise;
  } }), session, () => Promise.resolve(), () => undefined);
  progress.start(thread.id, reply.turn.request_id); await vi.advanceTimersByTimeAsync(1000);
  progress.clear();
  expect(signal?.aborted).toBe(true);
  response.resolve({ request_id: reply.turn.request_id, state: "running", stage: "generation", elapsed_seconds: 4 });
  await vi.advanceTimersByTimeAsync(5000);
  expect(progress.phase.value).toBe("sending"); expect(progress.status.value).toBeNull();
  expect(progress.elapsed.value).toBe(0); expect(vi.getTimerCount()).toBe(0);
});

it("does not let an older status response overwrite a newer request", async () => {
  vi.useFakeTimers(); const session = createSessionState(sessionApi()); await session.refresh();
  const older = deferred<RequestStatus>();
  const progress = createRequestProgress(chatApi({ status: (_id, requestId) => requestId === reply.turn.request_id
    ? older.promise : Promise.resolve({ request_id: requestId, state: "running", stage: "retrieval", elapsed_seconds: 1 })
  }), session, () => Promise.resolve(), () => undefined);
  progress.start(thread.id, reply.turn.request_id); await vi.advanceTimersByTimeAsync(1000);
  progress.start(thread.id, thread.id); await vi.advanceTimersByTimeAsync(1000);
  older.resolve({ request_id: reply.turn.request_id, state: "running", stage: "authorization", elapsed_seconds: 5 });
  await vi.advanceTimersByTimeAsync(0);
  expect(progress.status.value?.request_id).toBe(thread.id); expect(progress.phase.value).toBe("retrieval");
  progress.clear(); expect(vi.getTimerCount()).toBe(0);
});

it("locally stops the elapsed clock without changing a server outcome", async () => {
  vi.useFakeTimers(); const session = createSessionState(sessionApi()); await session.refresh();
  const progress = createRequestProgress(chatApi(), session, () => Promise.resolve(), () => undefined);
  progress.start(thread.id, reply.turn.request_id); await vi.advanceTimersByTimeAsync(2000);
  expect(progress.elapsed.value).toBe(2);
  progress.stop("stopped"); await vi.advanceTimersByTimeAsync(5000);
  expect(progress.elapsed.value).toBe(2); expect(progress.phase.value).toBe("stopped");
  expect(vi.getTimerCount()).toBe(0);
});
