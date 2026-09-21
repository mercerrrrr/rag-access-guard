<script setup lang="ts">
defineProps<{
  description: string;
  title: string;
}>();
</script>

<template>
  <header class="page-header">
    <div class="page-header__copy">
      <h1>{{ title }}</h1>
      <p>{{ description }}</p>
    </div>
    <fluent-divider role="presentation" />
  </header>
</template>

<style scoped>
.page-header {
  padding: var(--space-5) var(--space-5) 0;
}

.page-header__copy {
  padding-block-end: var(--space-4);
}

.page-header h1 {
  margin: 0;
  color: var(--color-text-primary);
  font-size: var(--font-size-title);
  font-weight: var(--font-weight-semibold);
  line-height: var(--line-height-title);
}

.page-header p {
  max-inline-size: var(--measure-reading);
  margin: var(--space-1) 0 0;
  color: var(--color-text-secondary);
  font-size: var(--font-size-label);
  line-height: var(--line-height-label);
}

@media (max-width: 47.99rem) {
  .page-header {
    padding: var(--space-4) var(--space-4) 0;
  }

  .page-header h1 {
    font-size: var(--font-size-section);
    line-height: var(--line-height-section);
  }
}
</style>
