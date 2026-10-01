<script setup lang="ts">
import { ref, watch } from "vue";
import type { UserPatch, UserSummary } from "@/api/accessTypes";
const props = defineProps<{ user: UserSummary; busy?: boolean; currentUserId?: string }>();
const emit = defineEmits<{ save: [patch: UserPatch] }>();
const active = ref(props.user.is_active), admin = ref(props.user.is_admin), confirming = ref(false);
watch(() => props.user, user => { active.value = user.is_active; admin.value = user.is_admin; confirming.value = false; });
function request() { if (!props.busy) confirming.value = true; }
function save() { if (!props.busy && confirming.value) emit("save", { is_active: active.value, is_admin: admin.value }); }
</script>
<template>
  <form
    class="access-form"
    @submit.prevent="request"
  >
    <p>{{ user.display_name }} <span class="access-muted">({{ user.login }})</span></p>
    <label class="access-checkbox"><input
      v-model="active"
      type="checkbox"
      name="is_active"
      :disabled="busy || confirming"
    >Учётная запись активна</label>
    <label class="access-checkbox"><input
      v-model="admin"
      type="checkbox"
      name="is_admin"
      :disabled="busy || confirming"
    >Административные полномочия</label>
    <p class="access-muted">
      Полномочия администратора не дают разрешения на чтение документов.
    </p>
    <button
      type="submit"
      class="session-button"
      :disabled="busy || confirming || (active === user.is_active && admin === user.is_admin)"
    >
      Сохранить полномочия
    </button>
    <div
      v-if="confirming"
      class="access-confirm"
      role="group"
      aria-label="Подтверждение изменения пользователя"
    >
      <p>Изменить пользователя «{{ user.display_name }}» ({{ user.login }})?</p>
      <p>Учётная запись: {{ active ? "активна" : "отключена" }}. Административные полномочия: {{ admin ? "включены" : "отключены" }}.</p>
      <p v-if="user.id === currentUserId && (!active || !admin)">
        Вы изменяете свою учётную запись. Панель управления станет недоступна.
      </p>
      <div class="access-actions">
        <button
          type="button"
          class="session-button"
          data-action="confirm-user"
          :disabled="busy"
          @click="save"
        >
          Подтвердить изменение
        </button>
        <button
          type="button"
          class="session-button"
          :disabled="busy"
          @click="confirming = false"
        >
          Отмена
        </button>
      </div>
    </div>
  </form>
</template>
