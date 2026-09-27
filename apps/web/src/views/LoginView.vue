<script setup lang="ts">
import { onBeforeUnmount, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import { useSession } from "@/composables/sessionContext";
import { safeReturnPath } from "@/routePaths";

const session = useSession();
const route = useRoute();
const router = useRouter();
const login = ref("");
const password = ref("");
onBeforeUnmount(() => { password.value = ""; });

async function submit() {
  if (session.busy.value) return;
  const pending = session.signIn(login.value, password.value);
  password.value = "";
  await pending;
  if (session.status.value === "authenticated" && router.currentRoute.value.name === "login") {
    await router.replace(safeReturnPath(route.query["returnTo"]));
  }
}
</script>

<template>
  <section
    class="session-panel"
    aria-labelledby="login-title"
  >
    <p class="session-panel__brand">
      Корпоративный поиск
    </p>
    <h1 id="login-title">
      Вход
    </h1>
    <p class="session-panel__hint">
      Используйте учётную запись, выданную администратором.
    </p>
    <form
      class="session-form"
      :aria-busy="session.busy.value"
      @submit.prevent="submit"
    >
      <div class="session-field">
        <label for="login">Логин</label>
        <input
          id="login"
          v-model="login"
          name="username"
          autocomplete="username"
          required
          maxlength="254"
          :disabled="session.busy.value"
          autocapitalize="none"
          :spellcheck="false"
        >
      </div>
      <div class="session-field">
        <label for="password">Пароль</label>
        <input
          id="password"
          v-model="password"
          name="password"
          type="password"
          autocomplete="current-password"
          required
          maxlength="1024"
          :disabled="session.busy.value"
        >
      </div>
      <p
        v-if="session.message.value"
        class="session-error"
        role="alert"
      >
        {{ session.message.value }}
      </p>
      <button
        class="session-button session-button--primary"
        type="submit"
        :disabled="session.busy.value"
      >
        {{ session.busy.value ? "Вход…" : "Войти" }}
      </button>
    </form>
    <p class="session-panel__footer">
      RAG Access Guard · Исследовательский прототип
    </p>
  </section>
</template>
