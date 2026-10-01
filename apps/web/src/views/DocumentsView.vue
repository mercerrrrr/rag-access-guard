<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { documentsApi } from "@/api/documents";
import type { DocumentUpload as UploadValue } from "@/api/documentTypes";
import { useSession } from "@/composables/sessionContext";
import { createDocumentsState } from "@/composables/useDocuments";
import PageHeader from "@/components/PageHeader.vue";
import DocumentList from "@/components/DocumentList.vue";
import DocumentUpload from "@/components/DocumentUpload.vue";
import DocumentVersionList from "@/components/DocumentVersionList.vue";

const session = useSession();
const state = createDocumentsState(documentsApi, session);
const admin = computed(() => session.user.value?.is_admin === true);
const administrative = computed(() => {
  const selected = state.selected.value;
  return selected !== null && "is_active" in selected ? selected : null;
});
const title = ref("");
watch(state.selected, (selected) => { title.value = selected?.title ?? ""; });
onMounted(() => { void state.refresh(); });
onBeforeUnmount(state.dispose);

async function rename() {
  const selected = administrative.value;
  if (selected !== null && title.value.trim()) {
    await state.update({ documentId: selected.id, patch: { title: title.value.trim() } });
  }
}
async function toggleActive() {
  const selected = administrative.value;
  if (selected !== null) await state.update({ documentId: selected.id, patch: { is_active: !selected.is_active } });
}
async function uploadVersion(value: UploadValue) {
  const selected = administrative.value;
  if (selected !== null) await state.uploadVersion({ documentId: selected.id, file: value.file });
}
</script>

<template>
  <div class="documents-view">
    <PageHeader
      title="Документы"
      :description="admin
        ? 'Управление документами и версиями. Чтение содержимого требует отдельного разрешения.'
        : 'Документы, доступные вашей учётной записи. Права проверяются при каждом открытии.'"
    />
    <div class="documents-workspace">
      <div class="documents-toolbar">
        <button
          type="button"
          class="session-button"
          data-action="refresh-documents"
          :disabled="state.busy.value"
          @click="state.refresh"
        >
          Обновить реестр
        </button>
        <p
          v-if="state.busy.value"
          role="status"
        >
          Запрос выполняется…
        </p>
      </div>
      <p
        v-if="state.message.value"
        class="session-error"
        role="alert"
      >
        {{ state.message.value }}
      </p>
      <details
        v-if="admin"
        class="documents-upload"
      >
        <summary>Загрузить документ</summary>
        <DocumentUpload
          :busy="state.busy.value"
          @upload="state.upload"
        />
      </details>
      <div class="documents-columns">
        <DocumentList
          :documents="state.items.value"
          :selected-id="state.selected.value?.id ?? null"
          :busy="state.busy.value"
          @select="state.select"
        />
        <section
          v-if="state.selected.value"
          class="document-detail"
          aria-labelledby="document-title"
        >
          <h2 id="document-title">
            {{ state.selected.value.title }}
          </h2>
          <p class="document-detail__id">
            {{ state.selected.value.id }}
          </p>
          <template v-if="administrative">
            <form
              class="session-form"
              @submit.prevent="rename"
            >
              <div class="session-field">
                <label for="document-rename">Название</label>
                <input
                  id="document-rename"
                  v-model="title"
                  required
                  maxlength="512"
                  :disabled="state.busy.value"
                >
              </div>
              <div class="documents-toolbar">
                <button
                  type="submit"
                  class="session-button"
                  :disabled="state.busy.value"
                >
                  Сохранить название
                </button>
                <button
                  type="button"
                  class="session-button"
                  :disabled="state.busy.value"
                  @click="toggleActive"
                >
                  {{ administrative.is_active ? "Отключить документ" : "Включить документ" }}
                </button>
              </div>
            </form>
            <p
              v-if="!state.canReadContent.value"
              class="document-detail__hint"
            >
              Нет разрешения на чтение содержимого.
            </p>
            <details
              v-if="administrative.is_active"
              class="documents-upload"
            >
              <summary>Загрузить новую версию</summary>
              <DocumentUpload
                :key="administrative.id"
                :busy="state.busy.value"
                version
                @upload="uploadVersion"
              />
            </details>
            <DocumentVersionList
              :versions="state.versions.value"
              :can-read-content="state.canReadContent.value"
              :active-version-id="state.selected.value.active_version_id"
              :busy="state.busy.value"
              @open-content="state.read"
            />
          </template>
          <button
            v-else-if="state.canReadContent.value"
            type="button"
            class="session-button"
            data-action="read-content"
            :disabled="state.busy.value"
            @click="state.read"
          >
            Открыть текст
          </button>
          <section
            v-if="state.content.value"
            data-document-content
            aria-labelledby="document-content-title"
            class="document-content"
          >
            <div class="documents-toolbar">
              <h2 id="document-content-title">
                Текст документа
              </h2>
              <button
                type="button"
                class="session-button"
                @click="state.closeContent"
              >
                Закрыть текст
              </button>
            </div>
            <p class="document-detail__id">
              Версия {{ state.content.value.document_version_id }}
            </p>
            <p class="document-content__text">
              {{ state.content.value.text }}
            </p>
          </section>
        </section>
        <p
          v-else-if="!state.busy.value"
          class="document-detail__hint"
        >
          Выберите документ в реестре.
        </p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.documents-workspace { display: grid; gap: var(--space-5); padding: var(--space-5); overflow-wrap: anywhere; }
.documents-toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-3); }
.documents-toolbar p { margin: 0; color: var(--color-text-secondary); }
.documents-columns { display: grid; grid-template-columns: minmax(0, 1fr); gap: var(--space-6); }
.document-detail { min-inline-size: 0; }
.document-detail h2 { margin-block: 0 var(--space-3); font-size: var(--font-size-section); }
.document-detail__id { color: var(--color-text-secondary); font-size: var(--font-size-label); overflow-wrap: anywhere; }
.document-detail__hint { color: var(--color-text-secondary); }
.documents-upload { border-block: var(--border-size-thin) solid var(--color-border); padding-block: var(--space-3); }
.document-detail .documents-upload { margin-block: var(--space-5); }
.documents-upload summary { min-block-size: var(--size-control); align-content: center; cursor: pointer; font-weight: var(--font-weight-medium); }
.documents-upload summary:focus-visible { outline: var(--size-focus-ring) solid var(--color-accent); }
.document-content { margin-block-start: var(--space-5); border-block-start: var(--border-size-thin) solid var(--color-border); padding-block-start: var(--space-5); }
.document-content__text { white-space: pre-wrap; overflow-wrap: anywhere; }
@media (min-width: 64rem) { .documents-columns { grid-template-columns: minmax(0, 1fr) minmax(0, 2fr); } }
@media (max-width: 47.99rem) { .documents-workspace { padding: var(--space-4); } }
</style>
