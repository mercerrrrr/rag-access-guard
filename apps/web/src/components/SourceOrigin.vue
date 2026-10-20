<script setup lang="ts">
import type { DocumentOrigin } from "@/api/origin";
defineProps<{ readonly origin: DocumentOrigin | null }>();
function dateLabel(value: string): string {
  return new Date(value).toLocaleDateString("ru-RU", { timeZone: "UTC" });
}
</script>

<template>
  <span class="source-origin">
    <template v-if="origin">
      <span v-if="origin.kind === 'synthetic_demo'">Вымышленный учебный пример, не документ МЭИ.</span>
      <span v-else-if="origin.kind === 'official_public'">Открытый материал</span>
      <span v-else>Загруженный документ</span>
      <span>{{ origin.publisher }}</span>
      <span v-if="origin.published_on">Опубликовано: {{ dateLabel(origin.published_on) }}</span>
      <span v-if="origin.retrieved_at">Снимок от {{ dateLabel(origin.retrieved_at) }}</span>
      <span v-if="origin.source_url">{{ origin.source_url }}</span>
    </template>
    <span v-else>Происхождение публикации не указано</span>
  </span>
</template>

<style scoped>
.source-origin { display: grid; gap: var(--space-1); color: var(--color-text-secondary); font-size: var(--font-size-label); line-height: var(--line-height-body); overflow-wrap: anywhere; }
</style>
