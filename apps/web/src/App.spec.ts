import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent } from "vue";
import {
  createMemoryHistory,
  createRouter,
  type RouteRecordRaw,
} from "vue-router";
import { describe, expect, it } from "vitest";

import App from "@/App.vue";
import { sessionKey } from "@/composables/sessionContext";
import { createSessionState } from "@/composables/useSession";
import { deferred, sessionApi, student } from "@/test/session";
import { ApiError } from "@/api/errors";

const createSectionComponent = (title: string) =>
  defineComponent({
    name: `${title}TestView`,
    template: `<h1>${title}</h1>`,
  });

const routes: RouteRecordRaw[] = [
  { path: "/login", name: "login", component: createSectionComponent("Вход") },
  { path: "/chat", component: createSectionComponent("Чат") },
  { path: "/documents", component: createSectionComponent("Документы") },
  { path: "/access", component: createSectionComponent("Доступ") },
  { path: "/audit", component: createSectionComponent("Аудит") },
];

describe("application shell", () => {
  it("keeps the four sections visible and marks the current route", async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes,
    });
    await router.push("/chat");
    await router.isReady();
    const session = createSessionState(sessionApi({ me: () => Promise.resolve({ user: { ...student, is_admin: true } }) }));
    await session.refresh();

    const wrapper = mount(App, {
      attachTo: document.body,
      global: {
        plugins: [router],
        provide: { [sessionKey]: session },
      },
    });

    const navigation = wrapper.get('nav[aria-label="Основные разделы"]');
    expect(navigation.text()).toContain("Чат");
    expect(navigation.text()).toContain("Документы");
    expect(navigation.text()).toContain("Доступ");
    expect(navigation.text()).toContain("Аудит");
    expect(navigation.get('a[href="/chat"]').attributes("aria-current")).toBe(
      "page",
    );

    await navigation.get('a[href="/documents"]').trigger("click");
    await flushPromises();

    expect(router.currentRoute.value.path).toBe("/documents");
    expect(navigation.get('a[href="/documents"]').attributes("aria-current")).toBe(
      "page",
    );
    expect(wrapper.get("main").text()).toContain("Документы");
  });
});

it("hides_admin_navigation_for_a_regular_session", async () => {
  const session = createSessionState(sessionApi());
  await session.refresh();
  const router = createRouter({ history: createMemoryHistory(), routes });
  await router.push("/chat");
  const wrapper = mount(App, { global: { plugins: [router], provide: { [sessionKey]: session } } });
  expect(wrapper.find('a[href="/access"]').exists()).toBe(false);
  expect(wrapper.find('a[href="/audit"]').exists()).toBe(false);
  expect(wrapper.text()).toContain(student.display_name);
  wrapper.unmount();
});

it("logout_immediately_removes_the_authenticated_shell", async () => {
  const pending = deferred<undefined>();
  const session = createSessionState(sessionApi({ logout: () => pending.promise }));
  await session.refresh();
  const router = createRouter({ history: createMemoryHistory(), routes });
  await router.push("/chat");
  const wrapper = mount(App, { global: { plugins: [router], provide: { [sessionKey]: session } } });
  const logout = session.signOut();
  await flushPromises();
  expect(wrapper.find('nav[aria-label="Основные разделы"]').exists()).toBe(false);
  expect(wrapper.text()).not.toContain(student.display_name);
  pending.resolve(undefined);
  await logout;
  wrapper.unmount();
});

it("forbidden_action_shows_feedback_without_logging_out", async () => {
  const session = createSessionState(sessionApi());
  await session.refresh();
  const router = createRouter({ history: createMemoryHistory(), routes });
  await router.push("/chat");
  const wrapper = mount(App, { global: { plugins: [router], provide: { [sessionKey]: session } } });
  await expect(session.runProtected(() => Promise.reject(new ApiError(403)))).rejects.toMatchObject({ status: 403 });
  await flushPromises();
  expect(wrapper.get('[role="alert"]').text()).not.toBe("");
  expect(wrapper.text()).toContain(student.display_name);
  wrapper.unmount();
});

it("allows_logout_while_identity_restore_is_pending", async () => {
  const pending = deferred<{ readonly user: typeof student }>();
  const session = createSessionState(sessionApi({ me: () => pending.promise }));
  const router = createRouter({ history: createMemoryHistory(), routes });
  await router.push("/chat");
  const refresh = session.refresh();
  const wrapper = mount(App, { global: { plugins: [router], provide: { [sessionKey]: session } } });
  await wrapper.get("button").trigger("click");
  await flushPromises();
  pending.resolve({ user: student });
  await refresh;
  expect(session.status.value).toBe("anonymous");
  expect(wrapper.text()).not.toContain(student.display_name);
  wrapper.unmount();
});
