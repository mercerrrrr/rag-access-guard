import { readonly, ref } from "vue";
import { ApiError } from "@/api/errors";
import { protectedResult } from "@/api/protectedResult";
import type { ChatApi, RequestStatus } from "@/api/types";
import type { SessionState } from "@/composables/useSession";

export type ProgressPhase = "sending" | "retrieval" | "generation" | "authorization"
  | "recovery" | "terminal" | "unavailable" | "expired" | "stopped";

export function createRequestProgress(api: ChatApi, session: SessionState,
  onTerminal: () => Promise<void>, onMissing: () => void, onExpired?: () => void) {
  const phase = ref<ProgressPhase>("sending");
  const status = ref<RequestStatus | null>(null);
  const elapsed = ref(0);
  let epoch = 0;
  let controller = new AbortController();
  let pollTimer: ReturnType<typeof setTimeout> | undefined;
  let clockTimer: ReturnType<typeof setTimeout> | undefined;

  function stop(next?: ProgressPhase) {
    epoch += 1; controller.abort();
    clearTimeout(pollTimer); clearTimeout(clockTimer);
    if (next !== undefined) phase.value = next;
  }
  function clear() { stop(); status.value = null; elapsed.value = 0; phase.value = "sending"; }
  function start(threadId: string, requestId: string) {
    clear(); controller = new AbortController();
    const current = epoch;
    function tick() {
      if (current !== epoch) return;
      elapsed.value += 1;
      clockTimer = setTimeout(tick, 1000);
    }
    async function poll() {
      try {
        const result = await session.runProtected(signal => protectedResult(() =>
          api.status(threadId, requestId, AbortSignal.any([signal, controller.signal]))));
        if (current !== epoch) return;
        if (!result.ok) {
          stop("unavailable");
          if (result.error.status === 404) onMissing();
          return;
        }
        status.value = result.value;
        switch (result.value.state) {
          case "running": phase.value = result.value.stage; break;
          case "absent": phase.value = "sending"; break;
          case "expired": stop("expired"); onExpired?.(); return;
          case "terminal": stop("terminal"); await onTerminal(); return;
        }
        pollTimer = setTimeout(() => { void poll(); }, 1000);
      } catch (error) {
        if (!(error instanceof ApiError)) throw error;
        if (current === epoch) stop("unavailable");
      }
    }
    clockTimer = setTimeout(tick, 1000);
    pollTimer = setTimeout(() => { void poll(); }, 1000);
  }
  return { phase: readonly(phase), status: readonly(status), elapsed: readonly(elapsed), start, stop, clear };
}
