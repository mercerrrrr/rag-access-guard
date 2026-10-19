import { readonly, ref } from "vue";
import { ApiError } from "@/api/errors";
import { protectedResult } from "@/api/protectedResult";
import type { AssistantGuidanceApi, DemoProfile, Suggestion, SystemStatus } from "@/api/demo";
import type { SessionState } from "@/composables/useSession";

export function createAssistantGuidance(api: AssistantGuidanceApi, session: SessionState) {
  const items = ref<readonly Suggestion[]>([]);
  const profile = ref<DemoProfile | null>(null);
  const system = ref<SystemStatus | null>(null);
  const loading = ref(false);
  const message = ref("");
  let epoch = 0;
  let disposed = false;
  let controller = new AbortController();

  function clear() {
    epoch += 1;
    controller.abort();
    controller = new AbortController();
    items.value = []; profile.value = null; system.value = null;
    loading.value = false; message.value = "";
  }
  const unsubscribe = session.onClear(clear);

  function isCurrent(current: number, sessionEpoch: number) {
    return !disposed && current === epoch && sessionEpoch === session.sessionEpoch.value;
  }

  async function refresh() {
    if (disposed) return;
    clear();
    if (session.status.value !== "authenticated" || session.protectedBlocked.value) return;
    const current = epoch;
    const sessionEpoch = session.sessionEpoch.value;
    loading.value = true;
    try {
      const result = await session.runProtected(signal => protectedResult(async () => {
        const combined = AbortSignal.any([signal, controller.signal]);
        const [nextItems, nextProfile, nextSystem] = await Promise.all([
          api.suggestions(combined), api.profile(combined), api.status(combined),
        ]);
        return { nextItems, nextProfile, nextSystem };
      }));
      if (!isCurrent(current, sessionEpoch)) return;
      if (result.ok) {
        items.value = result.value.nextItems.items;
        profile.value = result.value.nextProfile;
        system.value = result.value.nextSystem;
      } else {
        controller.abort();
        message.value = "Не удалось загрузить подсказки и состояние модели. Повторите обновление.";
      }
    } catch (error) {
      if (!isCurrent(current, sessionEpoch)) return;
      controller.abort();
      if (!(error instanceof ApiError)) throw error;
      message.value = "Не удалось загрузить подсказки и состояние модели. Повторите обновление.";
    } finally {
      if (isCurrent(current, sessionEpoch)) loading.value = false;
    }
  }
  function dispose() {
    disposed = true;
    clear();
    unsubscribe();
  }
  return { items: readonly(items), profile: readonly(profile), system: readonly(system),
    loading: readonly(loading), message: readonly(message), refresh, clear, dispose };
}
