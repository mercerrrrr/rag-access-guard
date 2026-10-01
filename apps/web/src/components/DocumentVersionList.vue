<script setup lang="ts">
import type { DocumentVersion } from "@/api/documentTypes";
defineProps<{
  versions: readonly DocumentVersion[];
  canReadContent: boolean;
  activeVersionId?: string | null;
  busy?: boolean;
}>();
defineEmits<{ "open-content": [versionId: string] }>();
const labels = { stored: "Сохранено", chunked: "Разбито на фрагменты", indexing: "Индексация",
  ready: "Готово", failed: "Ошибка индексации" } as const;
</script>

<template>
  <section aria-label="Версии документа">
    <h2>Версии документа</h2>
    <p v-if="versions.length === 0">
      Версий пока нет.
    </p>
    <ol class="versions">
      <li
        v-for="version in versions"
        :key="version.id"
      >
        <p><strong>{{ labels[version.status] }}</strong> · {{ version.id === activeVersionId ? "Активная версия" : "Неактивная версия" }}</p>
        <dl>
          <dt>Идентификатор</dt><dd>{{ version.id }}</dd>
          <dt>Создана</dt><dd>{{ new Date(version.created_at).toLocaleString("ru-RU") }}</dd>
          <dt>Размер</dt><dd>{{ version.byte_size.toLocaleString("ru-RU") }} байт</dd>
          <dt>SHA-256</dt><dd class="versions__hash">
            {{ version.content_sha256 }}
          </dd>
        </dl>
        <button
          v-if="canReadContent && version.id === activeVersionId"
          type="button"
          class="session-button"
          data-action="read-content"
          :disabled="busy"
          @click="$emit('open-content', version.id)"
        >
          Открыть текст
        </button>
      </li>
    </ol>
  </section>
</template>

<style scoped>
h2 { font-size: var(--font-size-section); }
.versions { list-style: none; padding: 0; margin: 0; }
.versions li { padding-block: var(--space-4); border-block-start: var(--border-size-thin) solid var(--color-border); }
.versions p { margin-block: 0 var(--space-3); }
.versions dl { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: var(--space-2) var(--space-4); font-size: var(--font-size-label); }
.versions dt { color: var(--color-text-secondary); }
.versions dd { margin: 0; overflow-wrap: anywhere; }
.versions__hash { font-family: var(--font-family-mono); }
@media (max-width: 47.99rem) {
  .versions dl { grid-template-columns: minmax(0, 1fr); }
}
</style>
