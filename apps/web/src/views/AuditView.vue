<script setup lang="ts">
import { onMounted, onUnmounted } from "vue";
import PageHeader from "@/components/PageHeader.vue";
import AuditFilters from "@/components/AuditFilters.vue";
import AuditTable from "@/components/AuditTable.vue";
import { auditApi, type AuditFilters as Filters } from "@/api/audit";
import { useSession } from "@/composables/sessionContext";
import { createAuditState } from "@/composables/useAudit";
import "@/styles/audit.css";
const session = useSession(), state = createAuditState(auditApi, session);
const { items, nextCursor, busy, message } = state;
let appliedFilters: Filters = {};
async function load(filters: Filters) { appliedFilters = filters; await state.load(filters); }
onMounted(() => { void load({}); });
onUnmounted(state.dispose);
</script>
<template>
  <PageHeader
    title="Аудит"
    description="Решения о доступе и изменения политики. Только метаданные, без содержимого документов и запросов."
  />
  <section
    v-if="session.user.value?.is_admin && !session.protectedBlocked.value"
    class="audit-workspace"
    aria-label="Журнал аудита"
  >
    <AuditFilters
      :busy="busy"
      @submit="load"
    />
    <div class="audit-actions">
      <button
        type="button"
        class="session-button"
        :disabled="busy"
        @click="load(appliedFilters)"
      >
        Обновить журнал
      </button>
      <p role="status">
        {{ busy ? 'Загрузка событий…' : `Загружено событий: ${items.length}` }}
      </p>
    </div>
    <p
      v-if="message"
      role="alert"
      class="session-error"
    >
      {{ message }}
    </p>
    <AuditTable
      v-if="items.length"
      :items="items"
    />
    <p
      v-else-if="!busy && !message"
      role="status"
    >
      События не найдены. Измените фильтры или период.
    </p>
    <button
      v-if="nextCursor"
      type="button"
      class="session-button"
      :disabled="busy"
      @click="state.loadMore"
    >
      Загрузить ещё
    </button>
  </section>
</template>
