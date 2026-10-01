<script setup lang="ts">
const input = defineModel<string>({ default: "" });
const props = defineProps<{ readonly disabled: boolean; readonly sending: boolean }>();
const emit = defineEmits<{ send: [input: string]; cancel: [] }>();
function submit() { if (!props.disabled && input.value.trim()) emit("send", input.value); }
function keydown(event: KeyboardEvent) {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    submit();
  }
}
</script>

<template>
  <form
    class="chat-composer"
    @submit.prevent="submit"
  >
    <label for="chat-question">Вопрос по документам</label>
    <textarea
      id="chat-question"
      v-model="input"
      rows="3"
      :disabled="disabled"
      aria-describedby="chat-input-hint"
      placeholder="Какие сведения нужно найти?"
      @keydown="keydown"
    />
    <div class="chat-composer__actions">
      <p
        id="chat-input-hint"
        class="chat-hint"
      >
        Enter — отправить, Shift+Enter — новая строка.
      </p>
      <button
        v-if="sending"
        class="session-button"
        type="button"
        @click="$emit('cancel')"
      >
        Остановить ожидание
      </button>
      <button
        class="session-button session-button--primary"
        type="submit"
        :disabled="disabled || !input.trim()"
      >
        {{ sending ? 'Ожидаем ответ…' : 'Отправить' }}
      </button>
    </div>
  </form>
</template>
