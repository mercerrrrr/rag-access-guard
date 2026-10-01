<script setup lang="ts">
import type { SourceView } from "@/api/types";
defineProps<{ readonly answer: string; readonly sources: readonly SourceView[]; readonly userInput: string;
  readonly selected?: boolean }>();
defineEmits<{ select: [] }>();
</script>

<template>
  <article class="chat-turn">
    <section aria-label="Вопрос">
      <h3>Вы</h3><p>{{ userInput }}</p>
    </section>
    <section aria-label="Ответ">
      <h3>Ответ</h3><p>{{ answer }}</p>
    </section>
    <button
      v-if="sources.length"
      class="session-button"
      type="button"
      :aria-pressed="selected ?? false"
      @click="$emit('select')"
    >
      Источники ответа ({{ sources.length }})
    </button>
  </article>
</template>
