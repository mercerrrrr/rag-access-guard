<script setup lang="ts">
import { ref, useId } from "vue";
import { docxMediaType } from "@/api/documentTypes";
const props = defineProps<{ busy: boolean; version?: boolean }>();
const emit = defineEmits<{ upload: [value: { readonly title: string; readonly file: File }] }>();
const id = useId();
const title = ref("");
const file = ref<File | null>(null);
const message = ref("");

function choose(event: Event) {
  if (!(event.target instanceof HTMLInputElement)) return;
  file.value = event.target.files?.item(0) ?? null;
  message.value = "";
}

function submit() {
  if (props.busy) return;
  const selected = file.value;
  if (selected === null) { message.value = "Выберите файл."; return; }
  if (selected.size === 0 || selected.size > 10485760) {
    message.value = "Файл должен быть непустым и не больше 10 МиБ."; return;
  }
  const extension = selected.name.toLowerCase().split(".").at(-1);
  const mime = selected.type.toLowerCase();
  const allowed = extension === "txt" ? ["text/plain"]
    : extension === "md" ? ["text/plain", "text/markdown"]
      : extension === "pdf" ? ["application/pdf"]
        : extension === "docx" ? [docxMediaType] : [];
  const defaultMime = allowed[0];
  if (defaultMime === undefined || (mime !== "" && !allowed.includes(mime))) {
    message.value = "Выберите TXT, Markdown, PDF или DOCX с подходящим типом файла."; return;
  }
  const normalizedTitle = title.value.trim();
  if (!props.version && (normalizedTitle.length === 0 || normalizedTitle.includes("\0"))) {
    message.value = "Укажите название документа."; return;
  }
  message.value = "";
  emit("upload", { title: normalizedTitle, file: mime === ""
    ? new File([selected], selected.name, { type: defaultMime }) : selected });
}
</script>

<template>
  <form
    class="document-upload session-form"
    :aria-busy="busy"
    @submit.prevent="submit"
  >
    <h2>{{ version ? "Новая версия" : "Загрузить документ" }}</h2>
    <div
      v-if="!version"
      class="session-field"
    >
      <label :for="`${id}-title`">Название документа</label>
      <input
        :id="`${id}-title`"
        v-model="title"
        type="text"
        required
        maxlength="512"
        :disabled="busy"
      >
    </div>
    <div class="session-field">
      <label :for="`${id}-file`">{{ version ? "Файл новой версии" : "Файл документа" }}</label>
      <input
        :id="`${id}-file`"
        type="file"
        accept=".txt,.md,.pdf,.docx"
        required
        :disabled="busy"
        :aria-describedby="`${id}-hint`"
        @change="choose"
      >
      <p
        :id="`${id}-hint`"
        class="document-upload__hint"
      >
        TXT, Markdown, текстовый PDF или DOCX. До 10 МиБ; PDF до 200 страниц, без OCR.
        DOCX: текстовые абзацы и обычные таблицы, без изображений и специальных объектов.
      </p>
    </div>
    <p
      v-if="message"
      class="session-error"
      role="alert"
    >
      {{ message }}
    </p>
    <button
      type="submit"
      class="session-button session-button--primary"
      :disabled="busy"
    >
      {{ busy ? "Обработка…" : version ? "Загрузить новую версию" : "Загрузить" }}
    </button>
  </form>
</template>

<style scoped>
.document-upload { max-inline-size: var(--measure-session); }
.document-upload h2 { margin: 0; font-size: var(--font-size-section); }
.document-upload__hint { margin: 0; font-size: var(--font-size-label); color: var(--color-text-secondary); }
.document-upload input[type="file"] { inline-size: 100%; }
.document-upload button { justify-self: start; }
</style>
