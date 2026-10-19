import { flushPromises, mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import { afterEach, expect, it, vi } from "vitest";
import { chatApi } from "@/api/chat";
import { demoApi } from "@/api/demo";
import { ApiError } from "@/api/errors";
import { sessionKey } from "@/composables/sessionContext";
import { createSessionState } from "@/composables/useSession";
import { thread } from "@/test/chat";
import { deferred, sessionApi } from "@/test/session";
import ChatView from "@/views/ChatView.vue";

const examples = { items: [{ id: "first", question: "<img src=x onerror=alert(1)> Какие правила?" }], policy_revision: 2 };
const profile = { enabled: false, title: "Помощник по базе знаний", subtitle: null,
  notice: "Исследовательский прототип", logo_url: null };
const status = { mode: "local", model: { name: "audited", state: "ready" }, search: { state: "ready" } };
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

async function setup() {
  vi.spyOn(chatApi, "list").mockResolvedValue([]);
  vi.spyOn(chatApi, "detail").mockResolvedValue(thread);
  const create = vi.spyOn(chatApi, "create").mockResolvedValue(thread);
  const send = vi.spyOn(chatApi, "send");
  vi.stubGlobal("fetch", (request: Request) => {
    const path = new URL(request.url).pathname;
    return Promise.resolve(Response.json(path.endsWith("suggestions") ? examples : path.endsWith("profile") ? profile : status));
  });
  const session = createSessionState(sessionApi());
  await session.refresh();
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: "/chat", name: "chat", component: ChatView, meta: { chat: true } },
    { path: "/chat/:threadId", name: "chat-thread", component: ChatView, meta: { chat: true } },
    { path: "/documents", component: { template: "<p>Документы</p>" } },
  ] });
  await router.push("/chat");
  const wrapper = mount(ChatView, { attachTo: document.body,
    global: { plugins: [router], provide: { [sessionKey]: session } } });
  await flushPromises();
  return { wrapper, router, session, create, send };
}

it("choosing_a_verified_question_only_fills_and_focuses_the_composer", async () => {
  const { wrapper, create, send } = await setup();
  try {
    expect(wrapper.text()).toContain("Локальная модель готова");
    const choice = wrapper.findAll("button").find(button => button.text() === examples.items[0]?.question);
    expect(choice).toBeDefined();
    await choice?.trigger("click");
    await flushPromises();
    expect(wrapper.get<HTMLTextAreaElement>("textarea").element.value).toBe(examples.items[0]?.question);
    expect(document.activeElement).toBe(wrapper.get("textarea").element);
    expect(wrapper.find("img").exists()).toBe(false);
    expect(create).not.toHaveBeenCalled(); expect(send).not.toHaveBeenCalled();
    expect(wrapper.get(".utility-header__title").text()).toBe("Помощник по базе знаний");
  } finally { wrapper.unmount(); }
});

it("focus_clears_old_suggestions_before_the_fresh_response_and_does_not_revive_after_logout", async () => {
  const { wrapper, session } = await setup();
  const pending = deferred<undefined>();
  vi.stubGlobal("fetch", (request: Request) => pending.promise.then(() => {
    const path = new URL(request.url).pathname;
    return Response.json(path.endsWith("suggestions") ? examples : path.endsWith("profile") ? profile : status);
  }));
  try {
    expect(wrapper.text()).toContain(examples.items[0]?.question);
    window.dispatchEvent(new Event("focus"));
    await flushPromises();
    expect(wrapper.text()).not.toContain(examples.items[0]?.question);
    await session.signOut();
    pending.resolve(undefined);
    await flushPromises();
    expect(wrapper.text()).not.toContain(examples.items[0]?.question);
    expect(wrapper.text()).not.toContain("Локальная модель готова");
  } finally { wrapper.unmount(); }
});

it("new_conversation_refetches_on_the_same_route_and_after_an_empty_saved_thread", async () => {
  const load = vi.spyOn(demoApi, "suggestions");
  const { wrapper, router } = await setup();
  try {
    expect(load).toHaveBeenCalledTimes(1);
    await wrapper.findAll("button").find(button => button.text() === "Новый")?.trigger("click");
    await flushPromises();
    expect(load).toHaveBeenCalledTimes(2);
    await router.push(`/chat/${thread.id}`); await flushPromises();
    expect(load).toHaveBeenCalledTimes(3);
    await wrapper.findAll("button").find(button => button.text() === "Новый")?.trigger("click");
    await flushPromises();
    expect(router.currentRoute.value.path).toBe("/chat"); expect(load).toHaveBeenCalledTimes(4);
  } finally { wrapper.unmount(); }
});

it("failed_refresh_discards_examples_and_preserves_the_unsent_draft", async () => {
  const { wrapper } = await setup();
  vi.spyOn(demoApi, "suggestions").mockRejectedValueOnce(new ApiError(503));
  try {
    await wrapper.get("textarea").setValue("Несохранённый вопрос");
    await wrapper.findAll("button").find(button => button.text() === "Обновить")?.trigger("click");
    await flushPromises();
    expect(wrapper.text()).not.toContain(examples.items[0]?.question);
    expect(wrapper.text()).toContain("Состояние модели неизвестно");
    expect(wrapper.get<HTMLTextAreaElement>("textarea").element.value).toBe("Несохранённый вопрос");
  } finally { wrapper.unmount(); }
});
