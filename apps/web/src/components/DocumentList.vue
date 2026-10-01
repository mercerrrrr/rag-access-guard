<script setup lang="ts">
import type { DocumentEntry } from "@/api/documentTypes";
defineProps<{ documents: readonly DocumentEntry[]; selectedId: string | null; busy: boolean }>();
defineEmits<{ select: [id: string] }>();
</script>
<template>
  <section aria-label="Реестр документов">
    <h2>Реестр документов</h2>
    <p v-if="documents.length === 0 && !busy">
      Документов нет.
    </p>
    <ul class="document-list">
      <li
        v-for="document in documents"
        :key="document.id"
      >
        <button
          type="button"
          data-action="select-document"
          :aria-pressed="selectedId === document.id"
          :disabled="busy"
          @click="$emit('select', document.id)"
        >
          <span class="document-list__title">{{ document.title }}</span>
          <span class="document-list__state">{{ 'is_active' in document ? document.is_active ? "Включён" : "Отключён" : "Доступен для чтения" }}</span>
          <span
            v-if="document.active_version_id === null"
            class="document-list__state"
          >Нет активной версии</span>
        </button>
      </li>
    </ul>
  </section>
</template>

<style scoped>
h2 { font-size: var(--font-size-section); }
.document-list { list-style: none; padding: 0; margin: 0; }
.document-list li { border-block-start: var(--border-size-thin) solid var(--color-border); }
.document-list button { display: grid; gap: var(--space-2); inline-size: 100%; min-block-size: var(--size-control); padding: var(--space-4); text-align: start; color: var(--color-text-primary); background: transparent; border: 0; cursor: pointer; overflow-wrap: anywhere; }
.document-list button:hover { background: var(--color-surface-subtle); }
.document-list button[aria-pressed="true"] { background: var(--color-accent-subtle); box-shadow: inset var(--size-active-indicator) 0 var(--color-accent); }
.document-list button:focus-visible { outline: var(--size-focus-ring) solid var(--color-accent); outline-offset: calc(-1 * var(--size-focus-ring)); }
.document-list button:disabled { cursor: wait; }
.document-list__title { font-weight: var(--font-weight-semibold); }
.document-list__state { color: var(--color-text-secondary); font-size: var(--font-size-label); }
.document-list button:hover .document-list__state,
.document-list button[aria-pressed="true"] .document-list__state { color: var(--color-text-primary); }
</style>
