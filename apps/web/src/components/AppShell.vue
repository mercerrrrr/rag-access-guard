<script setup lang="ts">
import { computed } from "vue";
import { useRoute } from "vue-router";

import NavigationIcon from "@/components/NavigationIcon.vue";

const route = useRoute();

const navigationItems = [
  { label: "Чат", to: "/chat", icon: "chat" },
  { label: "Документы", to: "/documents", icon: "documents" },
  { label: "Доступ", to: "/access", icon: "access" },
  { label: "Аудит", to: "/audit", icon: "audit" },
] as const;

const contextTitle = computed(() => route.meta.contextTitle);
const contextMessage = computed(() => route.meta.contextMessage);
</script>

<template>
  <a
    class="skip-link"
    href="#main-content"
  >К содержанию</a>

  <div
    class="app-shell"
    :class="{ 'app-shell--with-context': contextTitle !== undefined }"
  >
    <aside
      class="sidebar"
      aria-label="Навигация приложения"
    >
      <div
        class="sidebar__top"
        aria-hidden="true"
      />

      <nav
        class="navigation"
        aria-label="Основные разделы"
      >
        <RouterLink
          v-for="item in navigationItems"
          :key="item.to"
          class="navigation__link"
          active-class="navigation__link--active"
          :to="item.to"
        >
          <NavigationIcon :name="item.icon" />
          <span>{{ item.label }}</span>
        </RouterLink>
      </nav>

      <p class="sidebar__caption">
        Исследовательский прототип
      </p>
    </aside>

    <header class="utility-header">
      <span class="utility-header__title">Корпоративный поиск</span>
      <span class="utility-header__meta">RAG Access Guard</span>
    </header>

    <main
      id="main-content"
      class="app-main"
      tabindex="-1"
    >
      <slot />
    </main>

    <aside
      v-if="contextTitle !== undefined"
      class="context-rail"
      aria-labelledby="context-rail-title"
    >
      <div class="context-rail__header">
        <h2 id="context-rail-title">
          {{ contextTitle }}
        </h2>
      </div>
      <fluent-divider role="presentation" />
      <p class="context-rail__empty">
        {{ contextMessage }}
      </p>
    </aside>
  </div>
</template>

<style
  scoped
  src="../styles/app-shell.css"
></style>

<style
  scoped
  src="../styles/app-shell-responsive.css"
></style>
