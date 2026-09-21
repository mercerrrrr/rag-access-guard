import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent } from "vue";
import {
  createMemoryHistory,
  createRouter,
  type RouteRecordRaw,
} from "vue-router";
import { describe, expect, it } from "vitest";

import App from "@/App.vue";

const createSectionComponent = (title: string) =>
  defineComponent({
    name: `${title}TestView`,
    template: `<h1>${title}</h1>`,
  });

const routes: RouteRecordRaw[] = [
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

    const wrapper = mount(App, {
      attachTo: document.body,
      global: {
        plugins: [router],
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
