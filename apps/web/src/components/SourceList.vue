<script setup lang="ts">
import type { SourceView } from "@/api/types";
defineProps<{ readonly sources: readonly SourceView[]; readonly selected: string | null }>();
defineEmits<{ select: [source: SourceView] }>();
</script>

<template>
  <ol class="source-list">
    <li
      v-for="(source, index) in sources"
      :key="source.chunk_id"
    >
      <button
        type="button"
        class="source-list__button"
        :aria-pressed="selected === source.chunk_id"
        @click="$emit('select', source)"
      >
        <span class="source-list__number">[{{ index + 1 }}]</span>
        <span class="source-list__title">{{ source.title }}</span>
      </button>
    </li>
  </ol>
</template>
