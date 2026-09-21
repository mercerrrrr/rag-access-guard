<script setup lang="ts">
import PageHeader from "@/components/PageHeader.vue";
</script>

<template>
  <div class="preview">
    <PageHeader
      title="Проверка компонентов"
      description="Локальный стенд состояний оболочки. В production-сборку маршрут не входит."
    />

    <section
      class="preview__section"
      aria-labelledby="preview-buttons-title"
    >
      <h2 id="preview-buttons-title">
        Fluent controls
      </h2>
      <div class="preview__row">
        <fluent-button appearance="primary">
          Основное действие
        </fluent-button>
        <fluent-button appearance="outline">
          Вторичное действие
        </fluent-button>
        <fluent-button :disabled="true">
          Недоступно
        </fluent-button>
      </div>
    </section>

    <section
      class="preview__section"
      aria-labelledby="preview-colors-title"
    >
      <h2 id="preview-colors-title">
        Семантические поверхности
      </h2>
      <div class="preview__swatches">
        <div class="preview__swatch preview__swatch--canvas">
          Canvas
        </div>
        <div class="preview__swatch preview__swatch--sidebar">
          Sidebar
        </div>
        <div class="preview__swatch preview__swatch--accent">
          Accent
        </div>
        <div class="preview__swatch preview__swatch--danger">
          Danger
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.preview {
  min-block-size: 100%;
}

.preview__section {
  margin: var(--space-6) var(--space-5);
}

.preview__section h2 {
  margin: 0 0 var(--space-3);
  font-size: var(--font-size-section);
  line-height: var(--line-height-section);
}

.preview__row,
.preview__swatches {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
}

.preview__swatch {
  display: grid;
  min-inline-size: var(--size-swatch);
  min-block-size: var(--size-target);
  place-items: center;
  padding: var(--space-3);
  border: var(--border-size-thin) solid var(--color-border);
  border-radius: var(--radius-control);
  font-size: var(--font-size-label);
  font-weight: var(--font-weight-medium);
}

.preview__swatch--canvas {
  background: var(--color-canvas);
}

.preview__swatch--sidebar {
  background: var(--color-sidebar);
}

.preview__swatch--accent {
  border-color: var(--color-accent);
  color: var(--color-surface-primary);
  background: var(--color-accent);
}

.preview__swatch--danger {
  border-color: var(--color-danger);
  color: var(--color-surface-primary);
  background: var(--color-danger);
}

@media (max-width: 47.99rem) {
  .preview__section {
    margin: var(--space-5) var(--space-4);
  }
}
</style>
