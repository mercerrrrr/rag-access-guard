<script setup lang="ts">
import type { AuditEventView } from "@/api/audit";
import { eventLabels, stageLabels, outcomeLabels } from "@/auditLabels";
defineProps<{ items: readonly AuditEventView[] }>();
const timestamp = (value: string) => new Date(value).toISOString().replace("T", " ").replace("Z", " UTC");
</script>
<template>
  <table
    class="audit-table"
    role="table"
  >
    <caption>События от новых к старым</caption>
    <thead role="rowgroup">
      <tr role="row">
        <th
          scope="col"
          role="columnheader"
        >
          Время
        </th><th
          scope="col"
          role="columnheader"
        >
          Событие
        </th>
        <th
          scope="col"
          role="columnheader"
        >
          Результат
        </th><th
          scope="col"
          role="columnheader"
        >
          Идентификаторы
        </th>
      </tr>
    </thead>
    <tbody role="rowgroup">
      <tr
        v-for="item in items"
        :key="item.id"
        role="row"
      >
        <td role="cell">
          <span
            class="audit-mobile-label"
            aria-hidden="true"
          >Время</span>
          <time :datetime="item.occurred_at">{{ timestamp(item.occurred_at) }}</time>
        </td>
        <td role="cell">
          <span
            class="audit-mobile-label"
            aria-hidden="true"
          >Событие</span>
          <strong>{{ eventLabels[item.event_type] }}</strong><p>{{ stageLabels[item.stage] }}</p>
          <p class="audit-code">
            {{ item.id }}
          </p>
        </td>
        <td role="cell">
          <span
            class="audit-mobile-label"
            aria-hidden="true"
          >Результат</span>
          <span>{{ outcomeLabels[item.outcome] }}</span>
          <p>Ревизия {{ item.policy_revision }}</p><p>Источников: {{ item.source_count }}</p>
        </td>
        <td role="cell">
          <span
            class="audit-mobile-label"
            aria-hidden="true"
          >Идентификаторы</span>
          <dl class="audit-identifiers">
            <dt>Инициатор</dt><dd>{{ item.actor_user_id ?? 'Не указан' }}</dd>
            <dt>Пользователь</dt><dd>{{ item.principal_id ?? 'Не указан' }}</dd>
            <dt>Документ</dt><dd>{{ item.document_id ?? 'Не указан' }}</dd>
            <dt>Роль</dt><dd>{{ item.role_id ?? 'Не указана' }}</dd>
            <dt>Разрешение</dt><dd>{{ item.grant_id ?? 'Не указано' }}</dd>
          </dl>
        </td>
      </tr>
    </tbody>
  </table>
</template>
