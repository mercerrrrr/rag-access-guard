<script setup lang="ts">
defineProps<{
  heading: string;
  message: string;
}>();
</script>

<template>
  <section
    class="section-placeholder"
    aria-labelledby="section-placeholder-title"
  >
    <h2 id="section-placeholder-title">
      {{ heading }}
    </h2>
    <p>{{ message }}</p>
  </section>
</template>

<style scoped>
.section-placeholder {
  max-inline-size: var(--measure-content);
  margin: var(--space-8) var(--space-5);
  padding: var(--space-5);
  border: var(--border-size-thin) solid var(--color-border);
  border-radius: var(--radius-surface);
  background: var(--color-canvas);
}

.section-placeholder h2 {
  margin: 0;
  color: var(--color-text-primary);
  font-size: var(--font-size-section);
  font-weight: var(--font-weight-semibold);
  line-height: var(--line-height-section);
}

.section-placeholder p {
  max-inline-size: var(--measure-reading);
  margin: var(--space-2) 0 0;
  color: var(--color-text-secondary);
  overflow-wrap: anywhere;
}

@media (max-width: 47.99rem) {
  .section-placeholder {
    margin: var(--space-6) var(--space-4);
    padding: var(--space-4);
  }
}
</style>
