import { flushPromises, mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import { afterEach, expect, it, vi } from "vitest";
import { chatApi } from "@/api/chat";
import { ApiError } from "@/api/errors";
import { sessionKey } from "@/composables/sessionContext";
import { createSessionState } from "@/composables/useSession";
import { reply, source, thread } from "@/test/chat";
import { sessionApi } from "@/test/session";
import ChatView from "@/views/ChatView.vue";
import type { ThreadDetail } from "@/api/types";

afterEach(() => vi.restoreAllMocks());

it("successful_first_message_retry_clears_draft_and_opens_saved_route", async () => {
  let detail: ThreadDetail = thread;
  let attempts = 0;
  vi.spyOn(chatApi, "list").mockImplementation(() => Promise.resolve([detail]));
  vi.spyOn(chatApi, "create").mockResolvedValue(thread);
  vi.spyOn(chatApi, "detail").mockImplementation(() => Promise.resolve(detail));
  vi.spyOn(chatApi, "send").mockImplementation((_id, body) => {
    const turn = { ...reply.turn, request_id: body.request_id, user_input: body.user_input };
    detail = { ...thread, revision: 1, turns: [turn] };
    if (++attempts === 1) return Promise.reject(new ApiError(0));
    return Promise.resolve({ ...reply, turn, replayed: true });
  });
  const session = createSessionState(sessionApi());
  await session.refresh();
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: "/chat", name: "chat", component: ChatView, meta: { chat: true } },
    { path: "/chat/:threadId", name: "chat-thread", component: ChatView, meta: { chat: true } },
    { path: "/documents", component: { template: "<p>Документы</p>" } },
  ] });
  await router.push("/chat");
  const wrapper = mount(ChatView, { global: { plugins: [router], provide: { [sessionKey]: session } } });
  await flushPromises();
  await wrapper.get("textarea").setValue("Первый вопрос");
  await wrapper.get("form").trigger("submit");
  await flushPromises();
  const retry = wrapper.findAll("button").find(button => button.text() === "Повторить тот же запрос");
  expect(retry).toBeDefined();
  await retry?.trigger("click");
  await flushPromises();
  expect(wrapper.get<HTMLTextAreaElement>("textarea").element.value).toBe("");
  expect(router.currentRoute.value.path).toBe(`/chat/${thread.id}`);
  expect(attempts).toBe(2);
  wrapper.unmount();
});

it("unavailable_turn_keeps_only_user_question", async () => {
  let detail: ThreadDetail = { ...thread, revision: 1, turns: [reply.turn] };
  vi.spyOn(chatApi, "list").mockImplementation(() => Promise.resolve([detail]));
  vi.spyOn(chatApi, "detail").mockImplementation(() => Promise.resolve(detail));
  vi.spyOn(chatApi, "source").mockResolvedValue({ ...source, text: "SOURCE_BODY_53" });
  const session = createSessionState(sessionApi());
  await session.refresh();
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: "/chat/:threadId", component: ChatView, meta: { chat: true, contextTitle: "Источники ответа" } },
    { path: "/chat", component: ChatView, meta: { chat: true, contextTitle: "Источники ответа" } },
    { path: "/documents", component: { template: "<p>Документы</p>" } },
  ] });
  await router.push(`/chat/${thread.id}`);
  const wrapper = mount(ChatView, { global: { plugins: [router], provide: { [sessionKey]: session } } });
  await flushPromises();
  expect(wrapper.text()).toContain("Защищённый ответ");
  Object.defineProperty(wrapper.get(".chat-sources").element, "scrollIntoView", { value: () => undefined });
  await wrapper.findAll("button").find(button => button.text().startsWith("Источники ответа ("))?.trigger("click");
  await flushPromises();
  expect(wrapper.text()).toContain("Регламент");
  await wrapper.findAll("button").find(button => button.text().includes("Регламент"))?.trigger("click");
  await flushPromises();
  expect(wrapper.text()).toContain("SOURCE_BODY_53");
  detail = { ...detail, turns: [{ ...reply.turn, state: "unavailable", answer: null, sources: [],
    message: "Ответ недоступен: права на один из источников изменились." }] };

  await wrapper.findAll("button").find(button => button.text() === "Обновить")?.trigger("click");
  await flushPromises();

  expect(wrapper.get('[aria-label="Вопрос"]').text()).toContain("Вопрос");
  expect(wrapper.html()).not.toContain("Защищённый ответ");
  expect(wrapper.html()).not.toContain("Регламент");
  expect(wrapper.html()).not.toContain("SOURCE_BODY_53");
  expect(wrapper.html()).not.toContain(source.chunk_id);
  expect(wrapper.get('[aria-label="Ответ"] [role="status"]').text())
    .toBe("Ответ недоступен: права на один из источников изменились.");
  expect(wrapper.findAll("button").some(button => button.text().startsWith("Источники ответа ("))).toBe(false);
  wrapper.unmount();
});
