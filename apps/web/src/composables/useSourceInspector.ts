import { readonly, ref } from "vue";
import { ApiError } from "@/api/errors";
import { protectedResult } from "@/api/protectedResult";
import type { ChatApi, SourceContent, SourceView } from "@/api/types";
import type { SessionState } from "@/composables/useSession";

export function createSourceInspector(api: Pick<ChatApi, "source">, session: SessionState) {
  const content = ref<SourceContent | null>(null);
  const selected = ref<SourceView | null>(null);
  const busy = ref(false);
  const message = ref("");
  let epoch = 0;
  let controller = new AbortController();
  function clear() {
    epoch += 1;
    controller.abort();
    controller = new AbortController();
    content.value = null;
    selected.value = null;
    busy.value = false;
    message.value = "";
  }
  const unsubscribe = session.onClear(clear);
  async function open(source: SourceView) {
    clear();
    const current = epoch;
    selected.value = source;
    busy.value = true;
    try {
      const result = await session.runProtected((signal) => protectedResult(() =>
        api.source(source, AbortSignal.any([signal, controller.signal]))));
      if (current !== epoch) return;
      if (!result.ok) throw result.error;
      const value = result.value;
      if (value.document_id !== source.document_id || value.document_version_id !== source.document_version_id
        || value.chunk_id !== source.chunk_id) throw new ApiError(0);
      content.value = value;
    } catch (error) {
      if (current !== epoch) return;
      if (!(error instanceof ApiError)) throw error;
      content.value = null;
      selected.value = null;
      message.value = "Источник недоступен. Обновите диалог и выберите источник снова.";
    } finally { if (current === epoch) busy.value = false; }
  }
  return { content: readonly(content), selected: readonly(selected), busy: readonly(busy),
    message: readonly(message), open, clear, dispose: () => { clear(); unsubscribe(); } };
}
