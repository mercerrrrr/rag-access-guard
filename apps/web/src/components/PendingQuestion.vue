<script setup lang="ts">
import RequestProgress from "@/components/RequestProgress.vue";
import type { ProgressPhase } from "@/composables/useRequestProgress";
defineProps<{ readonly input: string; readonly phase: ProgressPhase; readonly elapsed: number }>();
</script>

<template>
  <article class="chat-turn">
    <section aria-label="Вопрос">
      <h3>Вы</h3><p>{{ input }}</p>
    </section>
    <section aria-label="Ответ">
      <h3>Ответ</h3><RequestProgress
        :phase="phase"
        :elapsed="elapsed"
      />
    </section>
  </article>
</template>
