<script setup lang="ts">
import { computed } from "vue";
import type { TurnView } from "@/api/types";
import UnavailableAnswer from "@/components/UnavailableAnswer.vue";
const props = defineProps<{ readonly turn: TurnView;
  readonly selected?: boolean }>();
defineEmits<{ select: [] }>();
const answer = computed(() => {
  const turn = props.turn;
  switch (turn.state) {
    case "available": return turn.answer;
    case "neutral": return turn.message;
    case "pending": return "Ответ ещё готовится. Обновите диалог позже.";
    case "unavailable": return null;
    default: {
      const unreachable: never = turn;
      return unreachable;
    }
  }
});
</script>

<template>
  <article class="chat-turn">
    <section aria-label="Вопрос">
      <h3>Вы</h3><p>{{ turn.user_input }}</p>
    </section>
    <section aria-label="Ответ">
      <h3>Ответ</h3>
      <UnavailableAnswer v-if="turn.state === 'unavailable'" />
      <p v-else>
        {{ answer }}
      </p>
    </section>
    <button
      v-if="turn.state === 'available'"
      class="session-button"
      type="button"
      :aria-pressed="selected ?? false"
      @click="$emit('select')"
    >
      Источники ответа ({{ turn.sources.length }})
    </button>
  </article>
</template>
