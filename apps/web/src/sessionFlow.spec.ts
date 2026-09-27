import { flushPromises, mount } from "@vue/test-utils";
import { createMemoryHistory } from "vue-router";
import { expect, it } from "vitest";

import App from "@/App.vue";
import { ApiError } from "@/api/errors";
import { sessionKey } from "@/composables/sessionContext";
import { createSessionState } from "@/composables/useSession";
import { createAppRouter } from "@/router";
import { deferred, sessionApi, student } from "@/test/session";

it("successful_login_returns_to_the_requested_known_route", async () => {
  let signedIn = false;
  const state = createSessionState(sessionApi({
    me: () => signedIn ? Promise.resolve({ user: student }) : Promise.reject(new ApiError(401)),
    login: () => { signedIn = true; return Promise.resolve({ user: student, csrf_token: "csrf" }); },
  }));
  const router = createAppRouter(createMemoryHistory(), state);
  await router.push("/documents");
  const wrapper = mount(App, { global: { plugins: [router], provide: { [sessionKey]: state } } });
  await wrapper.get('input[autocomplete="username"]').setValue("student");
  await wrapper.get('input[type="password"]').setValue("password");
  const navigation = deferred<undefined>();
  const unsubscribe = router.afterEach((to, _, failure) => {
    if (to.path === "/documents" && !failure) navigation.resolve(undefined);
  });
  await wrapper.get("form").trigger("submit");
  await navigation.promise;
  unsubscribe();
  await flushPromises();
  expect(router.currentRoute.value.path).toBe("/documents");
  expect(wrapper.find("form").exists()).toBe(false);
  expect(wrapper.text()).toContain(student.display_name);
  wrapper.unmount();
});

it("existing_server_session_requires_explicit_logout", async () => {
  const state = createSessionState(sessionApi({ login: () => Promise.reject(new ApiError(409)) }));
  await state.signIn("student", "password");
  expect(state.logoutPending.value).toBe(true);
  expect(state.status.value).toBe("unavailable");
  expect(state.user.value).toBeNull();
});

it("returning_focus_preserves_anonymous_login_form", async () => {
  const state = createSessionState(sessionApi({ me: () => Promise.reject(new ApiError(401)) }));
  const router = createAppRouter(createMemoryHistory(), state);
  await router.push("/login");
  const wrapper = mount(App, { global: { plugins: [router], provide: { [sessionKey]: state } } });
  await wrapper.get('input[autocomplete="username"]').setValue("student");
  await wrapper.get('input[type="password"]').setValue("password-manager-value");
  window.dispatchEvent(new Event("focus"));
  await flushPromises();
  expect(wrapper.get<HTMLInputElement>('input[autocomplete="username"]').element.value).toBe("student");
  expect(wrapper.get<HTMLInputElement>('input[type="password"]').element.value).toBe("password-manager-value");
  wrapper.unmount();
});
