<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import AppShell from "@/components/AppShell.vue";
import SessionStatus from "@/components/SessionStatus.vue";
import { useSession } from "@/composables/sessionContext";
import { safeReturnPath } from "@/routePaths";

const session = useSession();
const router = useRouter();
const route = useRoute();
const preview = computed(() => import.meta.env.DEV && route.name === "design-system");

watch(session.status, async (status) => {
  if (status === "anonymous" && route.matched.length > 0 && route.name !== "login" && !preview.value) {
    await router.replace({ name: "login", query: { returnTo: safeReturnPath(route.path) } });
  }
});

function revalidate() {
  if (!preview.value && !session.protectedBlocked.value && session.status.value === "authenticated"
    && document.visibilityState === "visible") void session.refresh();
}
function restorePage(event: PageTransitionEvent) {
  if (event.persisted && !preview.value && !session.protectedBlocked.value) void session.refresh();
}
onMounted(() => {
  document.addEventListener("visibilitychange", revalidate);
  window.addEventListener("pageshow", restorePage);
});
onBeforeUnmount(() => {
  document.removeEventListener("visibilitychange", revalidate);
  window.removeEventListener("pageshow", restorePage);
});
</script>

<template>
  <RouterView
    v-if="session.status.value === 'authenticated' && !session.protectedBlocked.value && route.meta.chat"
    :key="session.sessionEpoch.value"
  />
  <AppShell v-else-if="session.status.value === 'authenticated' || preview">
    <RouterView
      v-if="!session.protectedBlocked.value"
      :key="session.sessionEpoch.value"
    />
  </AppShell>
  <main
    v-else
    class="session-page"
  >
    <RouterView v-if="session.status.value === 'anonymous' && route.name === 'login'" />
    <SessionStatus v-else />
  </main>
</template>
