<script setup lang="ts">
import { useSession } from "@/composables/sessionContext";

const session = useSession();
</script>

<template>
  <section
    class="session-panel"
    aria-labelledby="session-title"
  >
    <p class="session-panel__brand">
      Корпоративный поиск
    </p>
    <h1 id="session-title">
      {{ session.status.value === "loading" ? "Проверка сессии" : "Сессия недоступна" }}
    </h1>
    <template v-if="session.status.value === 'loading'">
      <p role="status">
        Подождите…
      </p>
      <button
        v-if="!session.busy.value"
        class="session-button"
        type="button"
        @click="session.signOut"
      >
        Выйти
      </button>
    </template>
    <template v-else>
      <p
        class="session-error"
        role="alert"
      >
        {{ session.message.value }}
      </p>
      <button
        v-if="session.logoutPending.value"
        class="session-button"
        type="button"
        :disabled="session.busy.value"
        @click="session.signOut"
      >
        Повторить выход
      </button>
      <button
        v-else
        class="session-button"
        type="button"
        @click="session.refresh"
      >
        Проверить соединение
      </button>
    </template>
  </section>
</template>
