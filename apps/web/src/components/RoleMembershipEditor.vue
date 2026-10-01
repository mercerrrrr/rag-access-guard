<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import type { Role, UserSummary } from "@/api/accessTypes";
const props = defineProps<{ role: Role; users: readonly UserSummary[]; members: readonly UserSummary[]; busy?: boolean }>();
const emit = defineEmits<{ add: [userId: string]; remove: [userId: string] }>();
const pending = ref<UserSummary | null>(null), selected = ref("");
const heading = ref<HTMLElement | null>(null);
const available = computed(() => props.users.filter(user => !props.members.some(member => member.id === user.id)));
watch(() => props.role.id, () => { pending.value = null; selected.value = ""; });
watch(() => props.members, async members => {
  if (pending.value !== null && !members.some(member => member.id === pending.value?.id)) {
    pending.value = null; await nextTick(); heading.value?.focus();
  }
  if (!available.value.some(user => user.id === selected.value)) selected.value = "";
});
function add() { if (!props.busy && available.value.some(user => user.id === selected.value)) emit("add", selected.value); }
function remove() { if (!props.busy && pending.value !== null) emit("remove", pending.value.id); }
</script>
<template>
  <div>
    <h3
      ref="heading"
      tabindex="-1"
    >
      Участники роли «{{ role.display_name }}»
    </h3>
    <ul class="access-list">
      <li
        v-for="member in members"
        :key="member.id"
      >
        <span>{{ member.display_name }} <span class="access-muted">({{ member.login }})</span></span>
        <button
          type="button"
          class="session-button"
          :disabled="busy"
          @click="pending = member"
        >
          Удалить из роли
        </button>
      </li>
    </ul>
    <p
      v-if="members.length === 0 && !busy"
      class="access-muted"
    >
      У роли нет участников.
    </p>
    <div
      v-if="pending"
      class="access-confirm"
      role="group"
      aria-label="Подтверждение удаления участника"
    >
      <p>Удалить пользователя «{{ pending.display_name }}» ({{ pending.login }}) из роли «{{ role.display_name }}» ({{ role.code }})? Прямые разрешения и другие роли сохранятся.</p>
      <div class="access-actions">
        <button
          type="button"
          class="session-button"
          data-action="confirm-remove-member"
          :disabled="busy"
          @click="remove"
        >
          Подтвердить удаление
        </button>
        <button
          type="button"
          class="session-button"
          :disabled="busy"
          @click="pending = null"
        >
          Отмена
        </button>
      </div>
    </div>
    <form
      class="access-form"
      @submit.prevent="add"
    >
      <div class="session-field">
        <label for="member-user">Добавить пользователя</label>
        <select
          id="member-user"
          v-model="selected"
          required
          :disabled="busy || available.length === 0"
        >
          <option
            value=""
            disabled
          >
            Выберите пользователя
          </option>
          <option
            v-for="user in available"
            :key="user.id"
            :value="user.id"
          >
            {{ user.display_name }} ({{ user.login }})
          </option>
        </select>
      </div>
      <button
        type="submit"
        class="session-button"
        :disabled="busy || available.length === 0"
      >
        Добавить в роль
      </button>
    </form>
  </div>
</template>
