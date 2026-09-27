import { afterEach, expect, it, vi } from "vitest";

import { ApiError } from "@/api/errors";
import { createSessionState } from "@/composables/useSession";
import { deferred, sessionApi, student } from "@/test/session";

afterEach(() => vi.restoreAllMocks());

it("routine_revalidation_does_not_exhaust_csrf_bootstrap_limit", async () => {
  let bootstraps = 0;
  const state = createSessionState(sessionApi({
    csrf: () => ++bootstraps > 20
      ? Promise.reject(new ApiError(429, 600))
      : Promise.resolve({ csrf_token: "healthy-session-token" }),
  }));
  await state.signIn("student", "password");
  for (let index = 0; index < 25; index++) await state.refresh();
  expect(state.status.value).toBe("authenticated");
  expect(bootstraps).toBe(1);
});

it("explicit_refresh_recovers_csrf_after_forbidden_without_retrying_mutation", async () => {
  let bootstraps = 0;
  let mutations = 0;
  const state = createSessionState(sessionApi({
    csrf: () => { bootstraps += 1; return Promise.resolve({ csrf_token: "repaired-token" }); },
  }));
  await state.signIn("student", "password");
  await expect(state.runProtected(() => {
    mutations += 1;
    return Promise.reject(new ApiError(403));
  })).rejects.toMatchObject({ status: 403 });
  await state.refresh();
  expect(bootstraps).toBe(2);
  expect(mutations).toBe(1);
  expect(state.status.value).toBe("authenticated");
});

it("changed_identity_bootstraps_its_own_csrf_token", async () => {
  let currentUser = student;
  let bootstraps = 0;
  const state = createSessionState(sessionApi({
    me: () => Promise.resolve({ user: currentUser }),
    csrf: () => Promise.resolve({ csrf_token: `token-${String(++bootstraps)}` }),
  }));
  await state.refresh();
  currentUser = { ...student, id: "00000000-0000-4000-8000-000000000002" };
  await state.refresh();
  expect(bootstraps).toBe(2);
  expect(state.user.value?.id).toBe(currentUser.id);
});

it("restores_csrf_after_me_and_uses_it_for_logout", async () => {
  const calls: string[] = [];
  const state = createSessionState(sessionApi({
    me: () => { calls.push("me"); return Promise.resolve({ user: student }); },
    csrf: () => { calls.push("csrf"); return Promise.resolve({ csrf_token: "restored" }); },
    logout: (token) => { calls.push(token); return Promise.resolve(); },
  }));
  await state.refresh();
  await state.signOut();
  expect(calls).toEqual(["me", "csrf", "restored"]);
});

it("unauthorized_clears_all_protected_state", async () => {
  const state = createSessionState(sessionApi());
  await state.refresh();
  let protectedText = "protected";
  const unsubscribe = state.onClear(() => { protectedText = ""; });
  await expect(state.runProtected(() => Promise.reject(new ApiError(401))))
    .rejects.toMatchObject({ status: 401 });
  expect(protectedText).toBe("");
  expect(state.user.value).toBeNull();
  expect(state.status.value).toBe("anonymous");
  unsubscribe();
});

it("forbidden_keeps_identity_without_admin_content", async () => {
  const state = createSessionState(sessionApi());
  await state.refresh();
  let adminContent = "protected";
  state.onClear(() => { adminContent = ""; });
  await expect(state.runProtected(() => Promise.reject(new ApiError(403))))
    .rejects.toMatchObject({ status: 403 });
  expect(adminContent).toBe("");
  expect(state.user.value).toEqual(student);
  expect(state.status.value).toBe("authenticated");
});

it("late_protected_response_is_discarded_after_logout", async () => {
  const state = createSessionState(sessionApi());
  await state.refresh();
  const pending = deferred<string>();
  const read = state.runProtected(() => pending.promise);
  await state.signOut();
  pending.resolve("protected");
  await expect(read).rejects.toMatchObject({ status: 0 });
});

it("failed_login_has_no_password_in_error_or_storage", async () => {
  const store = vi.spyOn(Storage.prototype, "setItem");
  const state = createSessionState(sessionApi({ login: () => Promise.reject(new ApiError(401)) }));
  await state.signIn("student", "never-persist-this-password");
  expect(state.status.value).toBe("anonymous");
  expect(state.message.value).not.toContain("never-persist-this-password");
  expect(state.message.value).not.toBe("");
  expect(store).not.toHaveBeenCalled();
});

it("rate_limit_does_not_auto_retry_login", async () => {
  let attempts = 0;
  const state = createSessionState(sessionApi({
    login: () => { attempts += 1; return Promise.reject(new ApiError(429, 60)); },
  }));
  await state.signIn("student", "password");
  expect(attempts).toBe(1);
  expect(state.message.value).toContain("60");
  expect(state.status.value).toBe("anonymous");
});

it("each_explicit_login_obtains_a_new_challenge", async () => {
  let challenge = 0;
  const tokens: string[] = [];
  const state = createSessionState(sessionApi({
    csrf: () => Promise.resolve({ csrf_token: `challenge-${String(++challenge)}` }),
    login: (_, token) => { tokens.push(token); return Promise.reject(new ApiError(401)); },
  }));
  await state.signIn("student", "password");
  await state.signIn("student", "password");
  expect(tokens).toEqual(["challenge-1", "challenge-2"]);
});

it("duplicate_login_does_not_consume_another_challenge", async () => {
  const pending = deferred<{ readonly csrf_token: string }>();
  let attempts = 0;
  const state = createSessionState(sessionApi({
    csrf: () => { attempts += 1; return pending.promise; },
  }));
  const first = state.signIn("student", "password");
  await state.signIn("student", "password");
  pending.resolve({ csrf_token: "challenge" });
  await first;
  expect(attempts).toBe(1);
  expect(state.status.value).toBe("authenticated");
});

it("unsubscribed_cleanup_is_not_called", async () => {
  const state = createSessionState(sessionApi());
  let calls = 0;
  const unsubscribe = state.onClear(() => { calls += 1; });
  unsubscribe();
  await state.signOut();
  expect(calls).toBe(0);
});
