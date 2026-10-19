import { flushPromises, mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { chatApi } from "@/api/chat";
import { demoApi } from "@/api/demo";
import { ApiError } from "@/api/errors";
import type { MessageRequest, MessageResponse } from "@/api/types";
import { sessionKey } from "@/composables/sessionContext";
import { createSessionState } from "@/composables/useSession";
import { reply, thread } from "@/test/chat";
import { deferred, sessionApi } from "@/test/session";
import { profile, system } from "@/test/demo";
import ChatView from "@/views/ChatView.vue";

beforeEach(() => {
  vi.spyOn(demoApi, "suggestions").mockResolvedValue({ items: [], policy_revision: 0 });
  vi.spyOn(demoApi, "profile").mockResolvedValue(profile);
  vi.spyOn(demoApi, "status").mockResolvedValue(system);
  vi.spyOn(chatApi, "list").mockResolvedValue([thread]);
  vi.spyOn(chatApi, "create").mockResolvedValue(thread);
  vi.spyOn(chatApi, "detail").mockResolvedValue(thread);
  vi.spyOn(chatApi, "status").mockImplementation((_id, requestId) => Promise.resolve({
    request_id: requestId, state: "running", stage: "generation", elapsed_seconds: 1,
  }));
});
afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });
async function mountChat() {
  const session = createSessionState(sessionApi()); await session.refresh();
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: "/chat", name: "chat", component: ChatView },
    { path: "/chat/:threadId", name: "chat-thread", component: ChatView },
    { path: "/documents", component: { template: "<p>Документы</p>" } },
  ] });
  await router.push("/chat");
  const wrapper = mount(ChatView, { global: { plugins: [router], provide: { [sessionKey]: session } } });
  await flushPromises(); return { wrapper, router, session };
}

it("renders escaped pending input and hides welcome before completion", async () => {
  const post = deferred<MessageResponse>(); vi.spyOn(chatApi, "send").mockReturnValue(post.promise);
  const { wrapper } = await mountChat();
  const input = "<img src=x onerror=alert(1)>";
  await wrapper.get("textarea").setValue(input); await wrapper.get("form").trigger("submit"); await flushPromises();
  expect(wrapper.get('[aria-label="Вопрос"]').text()).toContain(input);
  expect(wrapper.find(".chat-empty").exists()).toBe(false);
  expect(wrapper.find(".chat-transcript img").exists()).toBe(false);
  expect(wrapper.text()).not.toContain(reply.turn.answer);
  wrapper.unmount(); post.resolve(reply); await flushPromises();
});

it("keeps elapsed time outside the polite stage region", async () => {
  vi.useFakeTimers(); const post = deferred<MessageResponse>();
  vi.spyOn(chatApi, "send").mockReturnValue(post.promise);
  const { wrapper } = await mountChat();
  await wrapper.get("textarea").setValue("Вопрос"); await wrapper.get("form").trigger("submit"); await flushPromises();
  await vi.advanceTimersByTimeAsync(1000); await flushPromises();
  const stage = wrapper.get('.request-progress [role="status"]');
  expect(stage.attributes("aria-live")).toBe("polite"); expect(stage.text()).toBe("Готовим ответ");
  expect(wrapper.get(".request-progress__elapsed").text()).toContain("1 с");
  expect(stage.find(".request-progress__elapsed").exists()).toBe(false);
  await vi.advanceTimersByTimeAsync(1000); await flushPromises();
  expect(stage.text()).toBe("Готовим ответ");
  wrapper.unmount(); post.resolve(reply); await flushPromises();
});

it("clears the first-question draft and opens history after lost POST recovery", async () => {
  vi.useFakeTimers(); let saved: MessageRequest | undefined;
  vi.spyOn(chatApi, "send").mockImplementation((_id, body) => { saved = body; return Promise.reject(new ApiError(0)); });
  vi.spyOn(chatApi, "status").mockImplementation((_id, requestId) => Promise.resolve({
    request_id: requestId, state: "terminal", stage: null, elapsed_seconds: null,
  }));
  vi.spyOn(chatApi, "detail").mockImplementation(() => Promise.resolve({ ...thread, revision: 1,
    turns: [{ ...reply.turn, request_id: saved?.request_id ?? "", user_input: "Вопрос" }] }));
  const { wrapper, router } = await mountChat();
  await wrapper.get("textarea").setValue("Вопрос"); await wrapper.get("form").trigger("submit"); await flushPromises();
  await vi.advanceTimersByTimeAsync(1000); await flushPromises();
  expect(wrapper.get<HTMLTextAreaElement>("textarea").element.value).toBe("");
  expect(router.currentRoute.value.path).toBe(`/chat/${thread.id}`);
  expect(wrapper.findAll('[aria-label="Вопрос"]')).toHaveLength(1); wrapper.unmount();
});

it("preserves draft and permits editing after a rejected admission", async () => {
  vi.spyOn(chatApi, "send").mockRejectedValue(new ApiError(429, 1, "inference_busy"));
  const { wrapper } = await mountChat();
  await wrapper.get("textarea").setValue("Вопрос  "); await wrapper.get("form").trigger("submit"); await flushPromises();
  expect(wrapper.get<HTMLTextAreaElement>("textarea").element.value).toBe("Вопрос  ");
  expect(wrapper.get("textarea").attributes("disabled")).toBeUndefined();
  expect(wrapper.findAll('[aria-label="Вопрос"]')).toHaveLength(0); wrapper.unmount();
});

it("offers enabled exact retry and history when status expires before POST settles", async () => {
  vi.useFakeTimers(); const post = deferred<MessageResponse>();
  vi.spyOn(chatApi, "send").mockReturnValue(post.promise);
  vi.spyOn(chatApi, "status").mockImplementation((_id, requestId) => Promise.resolve({
    request_id: requestId, state: "expired", stage: null, elapsed_seconds: null,
  }));
  const { wrapper } = await mountChat();
  await wrapper.get("textarea").setValue("Вопрос  "); await wrapper.get("form").trigger("submit"); await flushPromises();
  await vi.advanceTimersByTimeAsync(1000); await flushPromises();
  const actions = wrapper.findAll(".chat-retry button");
  expect(actions).toHaveLength(2); expect(actions.every(action => action.attributes("disabled") === undefined)).toBe(true);
  expect(actions.map(action => action.text())).toEqual(["Повторить тот же запрос", "Проверить историю и продолжить"]);
  expect(wrapper.get(".request-progress [role='status']").text()).toContain("истекло");
  expect(wrapper.get<HTMLTextAreaElement>("textarea").element.value).toBe("Вопрос  ");
  expect(wrapper.findAll('[aria-label="Вопрос"]')).toHaveLength(1);
  wrapper.unmount(); post.resolve(reply); await flushPromises();
});
