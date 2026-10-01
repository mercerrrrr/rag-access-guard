<script setup lang="ts">
import { ref } from "vue";
import { auditFiltersSchema, auditEventTypes, auditStages, auditOutcomes, type AuditFilters } from "@/api/audit";
import { eventLabels, stageLabels, outcomeLabels } from "@/auditLabels";
const props = defineProps<{ busy?: boolean }>();
const emit = defineEmits<{ submit: [filters: AuditFilters] }>();
const event = ref(""), stage = ref(""), outcome = ref(""), since = ref(""), until = ref(""), limit = ref(50), error = ref("");
function submit() {
  if (props.busy) return;
  const result = auditFiltersSchema.safeParse({
    ...(event.value ? { event_type: event.value } : {}), ...(stage.value ? { stage: stage.value } : {}),
    ...(outcome.value ? { outcome: outcome.value } : {}), ...(since.value ? { since: since.value } : {}),
    ...(until.value ? { until: until.value } : {}), limit: limit.value,
  });
  if (!result.success) { error.value = "Укажите корректное время с часовым поясом; начало должно быть раньше конца."; return; }
  error.value = ""; emit("submit", result.data);
}
function reset() {
  if (props.busy) return;
  event.value = ""; stage.value = ""; outcome.value = ""; since.value = ""; until.value = ""; limit.value = 50; error.value = "";
  emit("submit", {});
}
</script>
<template>
  <form
    class="audit-filters"
    novalidate
    aria-label="Фильтры журнала"
    @submit.prevent="submit"
    @reset.prevent="reset"
  >
    <div class="session-field">
      <label for="audit-event">Тип события</label>
      <select
        id="audit-event"
        v-model="event"
        name="event_type"
        :disabled="busy"
      >
        <option value="">
          Все события
        </option><option
          v-for="code in auditEventTypes"
          :key="code"
          :value="code"
        >
          {{ eventLabels[code] }}
        </option>
      </select>
    </div>
    <div class="session-field">
      <label for="audit-stage">Этап проверки</label>
      <select
        id="audit-stage"
        v-model="stage"
        name="stage"
        :disabled="busy"
      >
        <option value="">
          Все этапы
        </option><option
          v-for="code in auditStages"
          :key="code"
          :value="code"
        >
          {{ stageLabels[code] }}
        </option>
      </select>
    </div>
    <div class="session-field">
      <label for="audit-outcome">Результат</label>
      <select
        id="audit-outcome"
        v-model="outcome"
        name="outcome"
        :disabled="busy"
      >
        <option value="">
          Все результаты
        </option><option
          v-for="code in auditOutcomes"
          :key="code"
          :value="code"
        >
          {{ outcomeLabels[code] }}
        </option>
      </select>
    </div>
    <div class="session-field">
      <label for="audit-since">С момента (включительно)</label>
      <input
        id="audit-since"
        v-model="since"
        name="since"
        type="text"
        placeholder="2026-10-01T00:00:00+03:00"
        aria-describedby="audit-time-help"
        :disabled="busy"
      >
    </div>
    <div class="session-field">
      <label for="audit-until">До момента (исключая)</label>
      <input
        id="audit-until"
        v-model="until"
        name="until"
        type="text"
        placeholder="2026-10-02T00:00:00+03:00"
        aria-describedby="audit-time-help"
        :disabled="busy"
      >
    </div>
    <div class="session-field">
      <label for="audit-limit">Событий на страницу</label>
      <select
        id="audit-limit"
        v-model="limit"
        name="limit"
        :disabled="busy"
      >
        <option :value="25">
          25
        </option><option :value="50">
          50
        </option><option :value="100">
          100
        </option>
      </select>
    </div>
    <p
      id="audit-time-help"
      class="audit-filter-help"
    >
      Время ISO 8601 с поясом: Z означает UTC, +03:00 — московское время. Пустые поля не ограничивают период.
    </p>
    <p
      v-if="error"
      class="session-error audit-filter-help"
      role="alert"
    >
      {{ error }}
    </p>
    <div class="audit-actions audit-filter-help">
      <button
        type="submit"
        class="session-button session-button--primary"
        :disabled="busy"
      >
        Применить
      </button>
      <button
        type="reset"
        class="session-button"
        :disabled="busy"
      >
        Сбросить фильтры
      </button>
    </div>
  </form>
</template>
