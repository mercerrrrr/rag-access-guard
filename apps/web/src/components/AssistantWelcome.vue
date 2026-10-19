<script setup lang="ts">
import { computed } from "vue";
import type { DemoProfile, Suggestion, SystemStatus } from "@/api/demo";

const props = defineProps<{ readonly items: readonly Suggestion[]; readonly profile: DemoProfile | null;
  readonly system: SystemStatus | null; readonly loading: boolean; readonly message: string;
  readonly disabled?: boolean }>();
defineEmits<{ choose: [question: string] }>();
const modelMessages = {
  ready: "Локальная модель готова", test: "Тестовый режим: ответы модели отключены",
  disabled: "Модель недоступна", unavailable: "Модель недоступна",
} as const satisfies Record<SystemStatus["model"]["state"], string>;
const modelMessage = computed(() => {
  if (props.loading) return "Проверяем состояние модели…";
  if (props.system === null) return "Состояние модели неизвестно.";
  const { mode, model } = props.system;
  switch (mode) {
    case "test": return modelMessages.test;
    case "disabled": return modelMessages.disabled;
    case "local": return modelMessages[model.state];
    default: { const unreachable: never = mode; return unreachable; }
  }
});
</script>

<template>
  <section
    class="chat-empty"
    aria-labelledby="assistant-purpose"
  >
    <h2 id="assistant-purpose">
      {{ profile?.title ?? 'Помощник по базе знаний' }}
    </h2>
    <p>
      Задайте вопрос по доступным документам или выберите пример. Специальные команды не нужны.
      Источники ответа можно открыть в разделе «Источники ответа».
    </p>
    <p>{{ profile?.notice ?? 'Исследовательский прототип' }}</p>
    <p role="status">
      {{ modelMessage }}
    </p>
    <p v-if="system?.search.state === 'unavailable'">
      Поиск по документам недоступен.
    </p>
    <p
      v-if="message"
      role="alert"
    >
      {{ message }}
    </p>
    <ul
      v-if="items.length"
      class="assistant-suggestions"
      aria-label="Примеры вопросов по доступным документам"
    >
      <li
        v-for="item in items"
        :key="item.id"
      >
        <button
          class="session-button"
          type="button"
          :disabled="loading || disabled"
          @click="$emit('choose', item.question)"
        >
          {{ item.question }}
        </button>
      </li>
    </ul>
  </section>
</template>
