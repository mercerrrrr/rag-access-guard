import { flushPromises, mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import { expect, it } from "vitest";

import { ApiError } from "@/api/errors";
import { sessionKey } from "@/composables/sessionContext";
import { createSessionState } from "@/composables/useSession";
import { deferred, sessionApi } from "@/test/session";
import LoginView from "@/views/LoginView.vue";

it("submits_from_the_form_and_clears_password_on_failure", async () => {
  const session = createSessionState(sessionApi({ login: () => Promise.reject(new ApiError(401)) }));
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: "/login", component: LoginView }] });
  await router.push("/login");
  const wrapper = mount(LoginView, { global: { plugins: [router], provide: { [sessionKey]: session } } });
  await wrapper.get('input[autocomplete="username"]').setValue("student");
  await wrapper.get('input[autocomplete="current-password"]').setValue("private-password");
  await wrapper.get("form").trigger("submit");
  await flushPromises();
  expect(wrapper.get<HTMLInputElement>('input[type="password"]').element.value).toBe("");
  expect(wrapper.get('[role="alert"]').text()).not.toContain("private-password");
  expect(wrapper.get('[role="alert"]').text()).not.toBe("");
});

it("disables_duplicate_submission_while_pending", async () => {
  const pending = deferred<{ readonly csrf_token: string }>();
  const session = createSessionState(sessionApi({ csrf: () => pending.promise }));
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: "/login", component: LoginView }] });
  await router.push("/login");
  const wrapper = mount(LoginView, { global: { plugins: [router], provide: { [sessionKey]: session } } });
  await wrapper.get('input[autocomplete="username"]').setValue("student");
  await wrapper.get('input[type="password"]').setValue("password");
  await wrapper.get("form").trigger("submit");
  expect(wrapper.get<HTMLButtonElement>('button[type="submit"]').element.disabled).toBe(true);
  wrapper.unmount();
  pending.resolve({ csrf_token: "csrf" });
  await flushPromises();
});
