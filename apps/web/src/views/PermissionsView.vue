<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { accessApi } from "@/api/access";
import type { Grant, UserPatch } from "@/api/accessTypes";
import { useSession } from "@/composables/sessionContext";
import { createAccessState } from "@/composables/useAccess";
import PageHeader from "@/components/PageHeader.vue";
import PermissionEditor from "@/components/PermissionEditor.vue";
import RoleMembershipEditor from "@/components/RoleMembershipEditor.vue";
import UserCapabilityEditor from "@/components/UserCapabilityEditor.vue";
import "@/styles/access.css";

const session = useSession(), route = useRoute();
const state = createAccessState(accessApi, session);
const documentId = ref(""), roleId = ref(""), userId = ref("");
const roleCode = ref(""), roleName = ref(""), renamedRole = ref("");
const pendingGrant = ref<Grant | null>(null), grantHeading = ref<HTMLElement | null>(null);
const selectedDocument = computed(() => state.documents.value.find(item => item.id === state.selectedDocumentId.value));
const selectedRole = computed(() => state.roles.value.find(item => item.id === state.selectedRoleId.value));
const selectedUser = computed(() => state.users.value.find(item => item.id === userId.value));
watch(state.selectedDocumentId, id => { documentId.value = id ?? ""; pendingGrant.value = null; });
watch(state.selectedRoleId, id => { roleId.value = id ?? ""; });
watch(selectedRole, role => { renamedRole.value = role?.display_name ?? ""; });
onMounted(() => { void state.load(typeof route.query["document"] === "string" ? route.query["document"] : undefined); });
onBeforeUnmount(state.dispose);
function recipient(grant: Grant) {
  if (grant.user_id !== null) {
    const user = state.users.value.find(user => user.id === grant.user_id);
    return user ? `${user.display_name} (${user.login})` : grant.user_id;
  }
  const role = state.roles.value.find(role => role.id === grant.role_id);
  return role ? `${role.display_name} (${role.code})` : grant.role_id;
}
async function revoke() {
  const grant = pendingGrant.value;
  if (grant !== null && await state.revokeGrant(grant.id)) {
    pendingGrant.value = null; await nextTick(); grantHeading.value?.focus();
  }
}
async function createRole() {
  if (await state.createRole({ code: roleCode.value.trim(), display_name: roleName.value.trim() })) {
    roleCode.value = ""; roleName.value = "";
  }
}
async function saveUser(patch: UserPatch) { if (selectedUser.value) await state.updateUser(selectedUser.value.id, patch); }
</script>
<template>
  <div>
    <PageHeader
      title="Доступ"
      description="Прямые разрешения, роли и административные полномочия."
    />
    <div
      v-if="session.user.value?.is_admin"
      class="access-workspace"
      :aria-busy="state.busy.value"
    >
      <div class="access-actions">
        <button
          type="button"
          class="session-button"
          :disabled="state.busy.value"
          @click="state.load()"
        >
          Обновить данные
        </button>
        <p
          v-if="state.busy.value"
          role="status"
        >
          Запрос выполняется…
        </p>
      </div>
      <p
        v-if="state.message.value"
        role="alert"
        class="session-error"
      >
        {{ state.message.value }}
      </p>
      <section aria-labelledby="grants-heading">
        <h2
          id="grants-heading"
          ref="grantHeading"
          tabindex="-1"
        >
          Разрешения на документ
        </h2>
        <p class="access-muted">
          Для доступа достаточно прямого разрешения или разрешения любой текущей роли. Удаление одного основания не отменяет остальные.
        </p>
        <div class="session-field access-picker">
          <label for="grant-document">Документ</label>
          <select
            id="grant-document"
            v-model="documentId"
            :disabled="state.busy.value"
            @change="state.selectDocument(documentId)"
          >
            <option value="">
              Выберите документ
            </option>
            <option
              v-for="item in state.documents.value"
              :key="item.id"
              :value="item.id"
            >
              {{ item.title }}{{ item.is_active ? "" : " (отключён)" }}
            </option>
          </select>
        </div>
        <template v-if="selectedDocument">
          <p class="access-muted access-id">
            {{ selectedDocument.id }}
          </p>
          <div class="access-table-wrap">
            <table class="access-table">
              <caption>Разрешения: {{ selectedDocument.title }}</caption>
              <thead>
                <tr>
                  <th scope="col">
                    Основание
                  </th><th scope="col">
                    Получатель
                  </th><th scope="col">
                    Действие
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="grant in state.grants.value"
                  :key="grant.id"
                >
                  <td>{{ grant.user_id !== null ? "Прямое" : "Ролевое" }}</td><td>{{ recipient(grant) }}</td>
                  <td>
                    <button
                      type="button"
                      class="session-button"
                      :disabled="state.busy.value"
                      :aria-label="`Отозвать ${grant.user_id !== null ? 'прямое' : 'ролевое'} разрешение: ${recipient(grant)}`"
                      @click="pendingGrant = grant"
                    >
                      Отозвать
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <p
            v-if="state.grants.value.length === 0 && !state.busy.value"
            class="access-muted"
          >
            Нет выданных разрешений.
          </p>
          <div
            v-if="pendingGrant"
            class="access-confirm"
            role="group"
            aria-label="Подтверждение отзыва разрешения"
          >
            <p>Отозвать {{ pendingGrant.user_id !== null ? "прямое" : "ролевое" }} разрешение «{{ recipient(pendingGrant) }}» на документ «{{ selectedDocument.title }}»? Остальные разрешения сохранятся.</p>
            <div class="access-actions">
              <button
                type="button"
                class="session-button"
                data-action="confirm-revoke"
                :disabled="state.busy.value"
                @click="revoke"
              >
                Подтвердить отзыв
              </button>
              <button
                type="button"
                class="session-button"
                :disabled="state.busy.value"
                @click="pendingGrant = null"
              >
                Отмена
              </button>
            </div>
          </div>
          <h3>Выдать разрешение</h3>
          <PermissionEditor
            :document-id="selectedDocument.id"
            :users="state.users.value"
            :roles="state.roles.value"
            :busy="state.busy.value"
            @submit="state.createGrant"
          />
        </template>
      </section>
      <details class="access-section">
        <summary>Роли и участники</summary>
        <div class="access-columns">
          <section aria-labelledby="roles-heading">
            <h2 id="roles-heading">
              Состав роли
            </h2>
            <div class="session-field">
              <label for="selected-role">Роль для управления</label>
              <select
                id="selected-role"
                v-model="roleId"
                :disabled="state.busy.value"
                @change="state.selectRole(roleId)"
              >
                <option value="">
                  Выберите роль
                </option>
                <option
                  v-for="role in state.roles.value"
                  :key="role.id"
                  :value="role.id"
                >
                  {{ role.display_name }} ({{ role.code }})
                </option>
              </select>
            </div>
            <template v-if="selectedRole">
              <form
                class="access-form"
                @submit.prevent="state.renameRole(renamedRole.trim())"
              >
                <div class="session-field">
                  <label for="rename-role">Название роли</label><input
                    id="rename-role"
                    v-model="renamedRole"
                    required
                    maxlength="200"
                    :disabled="state.busy.value"
                  >
                </div>
                <button
                  type="submit"
                  class="session-button"
                  :disabled="state.busy.value"
                >
                  Сохранить название роли
                </button>
              </form>
              <RoleMembershipEditor
                :role="selectedRole"
                :users="state.users.value"
                :members="state.members.value"
                :busy="state.busy.value"
                @add="state.addMember"
                @remove="state.removeMember"
              />
            </template>
          </section>
          <section aria-labelledby="create-role-heading">
            <h2 id="create-role-heading">
              Новая роль
            </h2>
            <form
              class="access-form"
              @submit.prevent="createRole"
            >
              <div class="session-field">
                <label for="role-code">Код роли</label><input
                  id="role-code"
                  v-model="roleCode"
                  required
                  pattern="[a-z][a-z0-9_]{0,63}"
                  maxlength="64"
                  aria-describedby="role-code-hint"
                  :disabled="state.busy.value"
                ><p
                  id="role-code-hint"
                  class="access-muted"
                >
                  Строчные латинские буквы, цифры и подчёркивание. Первый символ — буква.
                </p>
              </div>
              <div class="session-field">
                <label for="role-name">Название новой роли</label><input
                  id="role-name"
                  v-model="roleName"
                  required
                  maxlength="200"
                  :disabled="state.busy.value"
                >
              </div>
              <button
                type="submit"
                class="session-button"
                :disabled="state.busy.value"
              >
                Создать роль
              </button>
            </form>
          </section>
        </div>
      </details>
      <details class="access-section">
        <summary>Пользователи и полномочия</summary>
        <p class="access-muted">
          Создание учётных записей выполняется локальной командой администратора. Изменение полномочий не выдаёт разрешений на документы.
        </p>
        <div class="session-field access-picker">
          <label for="selected-user">Пользователь для управления</label>
          <select
            id="selected-user"
            v-model="userId"
            :disabled="state.busy.value"
          >
            <option value="">
              Выберите пользователя
            </option>
            <option
              v-for="user in state.users.value"
              :key="user.id"
              :value="user.id"
            >
              {{ user.display_name }} ({{ user.login }}){{ user.is_active ? "" : " — отключён" }}
            </option>
          </select>
        </div>
        <UserCapabilityEditor
          v-if="selectedUser"
          :key="selectedUser.id"
          :user="selectedUser"
          :current-user-id="session.user.value.id"
          :busy="state.busy.value"
          @save="saveUser"
        />
      </details>
    </div>
    <p
      v-else
      class="access-workspace"
      role="status"
    >
      Административные полномочия недоступны.
    </p>
  </div>
</template>
