import { flushPromises, mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import { afterEach, expect, it, vi } from "vitest";
import { chatApi } from "@/api/chat";
import { ApiError } from "@/api/errors";
import { sessionKey } from "@/composables/sessionContext";
import { createSessionState } from "@/composables/useSession";
import { reply, thread } from "@/test/chat";
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
