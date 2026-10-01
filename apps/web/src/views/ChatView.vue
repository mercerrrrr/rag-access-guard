<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { chatApi } from "@/api/chat";
import { createChatState } from "@/composables/useChat";
import { useSession } from "@/composables/sessionContext";
import AppShell from "@/components/AppShell.vue";
import ChatComposer from "@/components/ChatComposer.vue";
import ChatTurn from "@/components/ChatTurn.vue";
import SourceList from "@/components/SourceList.vue";
import SourceInspector from "@/components/SourceInspector.vue";
import "@/styles/chat.css";

const route = useRoute();
const router = useRouter();
const chat = createChatState(chatApi, useSession());
const draft = ref("");
const inspectorPanel = ref<HTMLElement | null>(null);
watch(() => route.params["threadId"], async (id) => {
  draft.value = "";
  if (typeof id === "string") await chat.loadThread(id);
  else chat.newThread();
  await chat.listThreads();
}, { immediate: true });
onBeforeUnmount(chat.dispose);

async function chooseThread(event: Event) {
  if (!(event.target instanceof HTMLSelectElement)) return;
  await router.push(event.target.value ? `/chat/${event.target.value}` : "/chat");
}
async function refresh() {
  const id = chat.thread.value?.id ?? route.params["threadId"];
  if (typeof id === "string") await chat.loadThread(id);
  await chat.listThreads();
}
async function send(input: string) {
  await finishSubmission(await chat.sendQuestion(input));
}
async function retry() {
  await finishSubmission(await chat.retry());
}
async function finishSubmission(result: Awaited<ReturnType<typeof chat.sendQuestion>>) {
  if (!result || chat.thread.value?.id !== result.threadId || chat.retryRequest.value !== null) return;
  if (draft.value === result.userInput) draft.value = "";
  if (route.name === "chat") await router.replace(`/chat/${result.threadId}`);
}
async function selectTurn(id: string) {
  chat.selectTurn(id);
  await nextTick();
  inspectorPanel.value?.focus();
  inspectorPanel.value?.scrollIntoView({ block: "nearest" });
}
</script>

<template>
  <AppShell>
    <div class="chat-workspace">
      <header class="chat-header">
        <h1>{{ chat.thread.value?.title ?? 'Новый диалог' }}</h1>
        <div class="chat-toolbar">
          <label for="thread-choice">Диалог</label>
          <select
            id="thread-choice"
            :value="chat.thread.value?.id ?? ''"
            @change="chooseThread"
          >
            <option value="">
              Новый диалог
            </option>
            <option
              v-for="item in chat.threads.value"
              :key="item.id"
              :value="item.id"
            >
              {{ item.title }}
            </option>
          </select>
          <button
            class="session-button"
            type="button"
            @click="chat.newThread(); draft = ''; router.push('/chat')"
          >
            Новый
          </button>
          <button
            class="session-button"
            type="button"
            :disabled="chat.loading.value"
            @click="refresh"
          >
            Обновить
          </button>
        </div>
      </header>
      <div
        class="chat-transcript"
        :aria-busy="chat.loading.value"
      >
        <p
          v-if="chat.loading.value"
          role="status"
        >
          Загружаем диалог…
        </p>
        <div
          v-else-if="!chat.thread.value?.turns.length"
          class="chat-empty"
        >
          <h2>Задайте вопрос по доступным документам</h2>
          <p>Ответ появится целиком после проверки прав. Источники можно открыть в правой панели.</p>
        </div>
        <ChatTurn
          v-for="turn in chat.thread.value?.turns ?? []"
          :key="turn.id"
          :turn="turn"
          :selected="chat.selectedTurn.value === turn.id"
          @select="selectTurn(turn.id)"
        />
        <p
          v-if="chat.sending.value"
          class="chat-hint"
          role="status"
        >
          Готовим ответ. Он появится после завершения серверной проверки.
        </p>
      </div>
      <div
        v-if="chat.message.value"
        class="chat-feedback"
        role="status"
      >
        {{ chat.message.value }}
      </div>
      <div
        v-if="chat.retryRequest.value && !chat.sending.value"
        class="chat-retry"
      >
        <button
          class="session-button"
          type="button"
          :disabled="chat.loading.value"
          @click="retry"
        >
          Повторить тот же запрос
        </button>
        <button
          class="session-button"
          type="button"
          :disabled="chat.loading.value"
          @click="chat.loadThread(chat.retryRequest.value.threadId, true)"
        >
          Проверить историю и продолжить
        </button>
      </div>
      <ChatComposer
        v-model="draft"
        :disabled="chat.loading.value || chat.sending.value || chat.retryRequest.value !== null
          || (route.params['threadId'] !== undefined && chat.thread.value === null)"
        :sending="chat.sending.value"
        @send="send"
        @cancel="chat.cancel"
      />
    </div>
    <template #context>
      <div
        ref="inspectorPanel"
        class="chat-sources"
        tabindex="-1"
        aria-label="Источники выбранного ответа"
      >
        <p
          v-if="!chat.sources.value.length"
          class="chat-hint"
        >
          Выберите ответ с источниками, чтобы проверить его происхождение.
        </p>
        <SourceList
          :sources="chat.sources.value"
          :selected="chat.inspector.selected.value?.chunk_id ?? null"
          @select="chat.inspector.open"
        />
        <SourceInspector
          :content="chat.inspector.content.value"
          :busy="chat.inspector.busy.value"
          :message="chat.inspector.message.value"
          @close="chat.inspector.clear"
        />
      </div>
    </template>
  </AppShell>
</template>
