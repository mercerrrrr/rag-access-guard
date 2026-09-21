<script setup lang="ts">
defineProps<{
  name: "chat" | "documents" | "access" | "audit";
}>();
</script>

<template>
  <svg
    class="navigation-icon"
    viewBox="0 0 24 24"
    aria-hidden="true"
    fill="none"
    stroke="currentColor"
    stroke-width="1.75"
    stroke-linecap="round"
    stroke-linejoin="round"
  >
    <path
      v-if="name === 'chat'"
      d="M21 14.5a4 4 0 0 1-4 4H9l-5 3v-3.2A4 4 0 0 1 2 14.8V7a4 4 0 0 1 4-4h11a4 4 0 0 1 4 4Z"
    />
    <template v-else-if="name === 'documents'">
      <path d="M6 2.5h8l4 4V21.5H6Z" />
      <path d="M14 2.5v5h4" />
    </template>
    <template v-else-if="name === 'access'">
      <path d="M7.5 10V7.5a4.5 4.5 0 0 1 9 0V10" />
      <path d="M5 10h14v11.5H5Z" />
    </template>
    <template v-else>
      <path d="M5 21v-7" />
      <path d="M12 21V3" />
      <path d="M19 21V9" />
    </template>
  </svg>
</template>

<style scoped>
.navigation-icon {
  flex: 0 0 auto;
  inline-size: var(--size-icon);
  block-size: var(--size-icon);
}
</style>
