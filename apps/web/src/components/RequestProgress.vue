<script setup lang="ts">
import { computed } from "vue";
import type { ProgressPhase } from "@/composables/useRequestProgress";
const props = defineProps<{ readonly phase: ProgressPhase; readonly elapsed: number }>();
const label = computed(() => {
  switch (props.phase) {
    case "sending": return "Отправляем вопрос";
    case "retrieval": return "Ищем в доступных документах";
    case "generation": return "Готовим ответ";
    case "authorization": return "Проверяем права перед выдачей";
    case "recovery": return "Проверяем состояние запроса";
    case "terminal": return "Запрос завершён. Получаем сохранённый результат.";
    case "unavailable": return "Не удалось уточнить состояние";
    case "expired": return "Время ожидания запроса истекло. Проверьте историю или повторите тот же запрос.";
    case "stopped": return "Ожидание остановлено. Сервер мог продолжить обработку.";
    default: { const unreachable: never = props.phase; return unreachable; }
  }
});
</script>

<template>
  <div class="request-progress">
    <p
      role="status"
      aria-live="polite"
    >
      {{ label }}
    </p>
    <p class="request-progress__elapsed chat-hint">
      Время ожидания: {{ elapsed }} с
    </p>
  </div>
</template>
