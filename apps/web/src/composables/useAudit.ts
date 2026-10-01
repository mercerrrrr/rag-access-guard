import { ref, readonly } from "vue";
import type { AuditApi, AuditEventView, AuditFilters } from "@/api/audit";
import { auditFiltersSchema } from "@/api/audit";
import { ApiError } from "@/api/errors";
import { protectedResult } from "@/api/protectedResult";
import type { SessionState } from "@/composables/useSession";
export function createAuditState(api: AuditApi, session: SessionState) {
  const items = ref<readonly AuditEventView[]>([]), nextCursor = ref<string | null>(null);
  const busy = ref(false), message = ref("");
  let epoch = 0, disposed = false;
  let controller = new AbortController();
  let filters: AuditFilters = {};
  function clear() {
    epoch += 1; controller.abort(); controller = new AbortController();
    items.value = []; nextCursor.value = null; message.value = ""; busy.value = false; filters = {};
  }
  const unsubscribe = session.onClear(clear);
  async function fetchPage(cursor: string | null) {
    if (disposed || session.user.value?.is_admin !== true) return;
    const current = ++epoch;
    controller.abort(); controller = new AbortController();
    const localSignal = controller.signal;
    busy.value = true; message.value = "";
    try {
      const result = await session.runProtected(signal => protectedResult(() =>
        api.list(filters, cursor, AbortSignal.any([signal, localSignal]))));
      if (current !== epoch) return;
      if (!result.ok) throw result.error;
      items.value = cursor === null ? result.value.items : [...items.value, ...result.value.items];
      nextCursor.value = result.value.next_cursor;
    } catch (error) {
      if (current !== epoch) return;
      if (!(error instanceof ApiError)) throw error;
      message.value = error.status === 422 ? "Проверьте фильтры и диапазон времени."
        : "Не удалось загрузить журнал. Повторите запрос.";
    } finally { if (current === epoch) busy.value = false; }
  }
  async function load(value: AuditFilters) {
    clear();
    const checked = auditFiltersSchema.safeParse(value);
    if (!checked.success) { message.value = "Проверьте фильтры и диапазон времени."; return; }
    filters = checked.data; await fetchPage(null);
  }
  async function loadMore() { if (!busy.value && nextCursor.value !== null) await fetchPage(nextCursor.value); }
  return { items: readonly(items), nextCursor: readonly(nextCursor), busy: readonly(busy),
    message: readonly(message), load, loadMore, clear,
    dispose: () => { disposed = true; clear(); unsubscribe(); } };
}
