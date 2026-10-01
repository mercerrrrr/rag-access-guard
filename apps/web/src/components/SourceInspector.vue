<script setup lang="ts">
import type { SourceContent } from "@/api/types";
defineProps<{ readonly content: SourceContent | null; readonly busy: boolean; readonly message: string }>();
defineEmits<{ close: [] }>();
</script>

<template>
  <div
    class="source-inspector"
    :aria-busy="busy"
  >
    <p
      v-if="busy"
      role="status"
    >
      Проверяем доступ к источнику…
    </p>
    <p
      v-if="message"
      role="status"
    >
      {{ message }}
    </p>
    <section
      v-if="content"
      aria-label="Содержимое источника"
    >
      <div class="source-inspector__heading">
        <h3>{{ content.title }}</h3>
        <button
          type="button"
          class="session-button"
          @click="$emit('close')"
        >
          Закрыть
        </button>
      </div>
      <p class="chat-hint">
        ✓ Разрешён при открытии
      </p>
      <p class="source-inspector__text">
        {{ content.text }}
      </p>
    </section>
  </div>
</template>
