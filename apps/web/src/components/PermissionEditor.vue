<script setup lang="ts">
import { ref, watch } from "vue";
const props = defineProps<{ documentId: string; users: readonly { id: string; login: string; display_name: string }[];
  roles: readonly { id: string; code: string; display_name: string }[]; busy?: boolean }>();
const emit = defineEmits<{ submit: [target: { readonly user_id: string } | { readonly role_id: string }] }>();
const kind = ref("user"), userId = ref(""), roleId = ref("");
watch([kind, () => props.documentId], () => { userId.value = ""; roleId.value = ""; });
function submit() {
  if (props.busy) return;
  if (kind.value === "user" && props.users.some(user => user.id === userId.value)) emit("submit", { user_id: userId.value });
  if (kind.value === "role" && props.roles.some(role => role.id === roleId.value)) emit("submit", { role_id: roleId.value });
}
</script>
<template>
  <form
    class="access-form"
    @submit.prevent="submit"
  >
    <div class="session-field">
      <label for="grant-target-kind">Получатель</label>
      <select
        id="grant-target-kind"
        v-model="kind"
        name="target_kind"
        :disabled="busy"
      >
        <option value="user">
          Пользователь
        </option><option value="role">
          Роль
        </option>
      </select>
    </div>
    <div
      v-if="kind === 'user'"
      class="session-field"
    >
      <label for="grant-user">Пользователь</label>
      <select
        id="grant-user"
        v-model="userId"
        name="user_id"
        required
        :disabled="busy"
      >
        <option value="">
          Выберите пользователя
        </option><option
          v-for="user in users"
          :key="user.id"
          :value="user.id"
        >
          {{ user.display_name }} ({{ user.login }})
        </option>
      </select>
    </div>
    <div
      v-else
      class="session-field"
    >
      <label for="grant-role">Роль</label>
      <select
        id="grant-role"
        v-model="roleId"
        name="role_id"
        required
        :disabled="busy"
      >
        <option value="">
          Выберите роль
        </option><option
          v-for="role in roles"
          :key="role.id"
          :value="role.id"
        >
          {{ role.display_name }} ({{ role.code }})
        </option>
      </select>
    </div>
    <button
      type="submit"
      class="session-button"
      :disabled="busy"
    >
      Выдать разрешение
    </button>
  </form>
</template>
