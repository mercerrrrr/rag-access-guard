import { expect, it, vi } from "vitest";
import { ApiError } from "@/api/errors";
import type { SuggestionList } from "@/api/demo";
import { createAssistantGuidance } from "@/composables/useAssistantGuidance";
import { createSessionState } from "@/composables/useSession";
import { guidanceApi, suggestions, profile, system } from "@/test/demo";
import { deferred, sessionApi, student } from "@/test/session";

async function setup() {
  const session = createSessionState(sessionApi({ login: credentials => Promise.resolve({
    user: credentials.login === "other" ? { ...student, id: "00000000-0000-4000-8000-000000000002", login: "other" } : student,
    csrf_token: "new-session-token",
  }) }));
  await session.refresh();
  return session;
}

it("refresh_clears_previous_bundle_before_waiting_and_latest_request_wins", async () => {
  const session = await setup();
  const old = deferred<SuggestionList>();
  let calls = 0; let firstSignal: AbortSignal | undefined;
  const guidance = createAssistantGuidance(guidanceApi({ suggestions: signal => {
    calls += 1;
    if (calls === 2) { firstSignal = signal; return old.promise; }
    return Promise.resolve(suggestions);
  } }), session);
  await guidance.refresh();
  const pending = guidance.refresh();
  expect(guidance.items.value).toEqual([]); expect(guidance.profile.value).toBeNull();
  expect(guidance.system.value).toBeNull(); expect(guidance.loading.value).toBe(true);
  await guidance.refresh();
  expect(firstSignal?.aborted).toBe(true);
  old.resolve({ items: [{ id: "stale", question: "STALE" }], policy_revision: 1 });
  await pending;
  expect(guidance.items.value).toEqual(suggestions.items);
  expect(guidance.profile.value).toEqual(profile); expect(guidance.system.value).toEqual(system);
  guidance.dispose();
});

it.each(["clear", "logout", "another_login", "dispose"] as const)("late_result_cannot_revive_after_%s", async action => {
  const session = await setup(); const pending = deferred<SuggestionList>();
  let signal: AbortSignal | undefined;
  const guidance = createAssistantGuidance(guidanceApi({ suggestions: request => { signal = request; return pending.promise; } }), session);
  const loading = guidance.refresh();
  if (action === "dispose") guidance.dispose();
  else if (action === "clear") session.clearProtectedState();
  else {
    await session.signOut();
    if (action === "another_login") await session.signIn("other", "password");
  }
  pending.resolve(suggestions); await loading;
  expect(signal?.aborted).toBe(true); expect(guidance.items.value).toEqual([]);
  expect(guidance.profile.value).toBeNull(); expect(guidance.system.value).toBeNull();
  expect(guidance.message.value).toBe(""); expect(guidance.loading.value).toBe(false);
  guidance.dispose();
});

it.each([0, 404, 503])("endpoint_failure_%s_is_local_and_does_not_reuse_previous_data", async status => {
  const session = await setup(); let fail = false;
  const guidance = createAssistantGuidance(guidanceApi({ suggestions: () => fail
    ? Promise.reject(new ApiError(status)) : Promise.resolve(suggestions) }), session);
  await guidance.refresh(); fail = true; await guidance.refresh();
  expect(guidance.items.value).toEqual([]); expect(guidance.profile.value).toBeNull();
  expect(guidance.system.value).toBeNull(); expect(guidance.message.value).not.toBe("");
  expect(session.status.value).toBe("authenticated"); expect(session.user.value).toEqual(student);
  fail = false; await guidance.refresh(); expect(guidance.items.value).toEqual(suggestions.items);
  guidance.dispose();
});

it("no_config_is_an_empty_success_and_anonymous_or_disposed_state_does_not_fetch", async () => {
  const session = await setup();
  const load = vi.fn().mockResolvedValue({ items: [], policy_revision: 2 });
  const guidance = createAssistantGuidance(guidanceApi({ suggestions: load }), session);
  await guidance.refresh(); expect(guidance.items.value).toEqual([]); expect(guidance.message.value).toBe("");
  await session.signOut(); await guidance.refresh(); expect(load).toHaveBeenCalledTimes(1);
  guidance.dispose(); await session.signIn("student", "password"); await guidance.refresh();
  expect(load).toHaveBeenCalledTimes(1);
});

it.each([401, 403])("authorization_failure_%s_uses_session_cleanup", async status => {
  const session = await setup();
  const guidance = createAssistantGuidance(guidanceApi({ suggestions: () => Promise.reject(new ApiError(status)) }), session);
  await guidance.refresh();
  expect(guidance.items.value).toEqual([]); expect(guidance.system.value).toBeNull();
  expect(status === 401 ? session.status.value === "anonymous" : session.protectedBlocked.value).toBe(true);
  guidance.dispose();
});

it("unknown_programming_failure_is_not_swallowed", async () => {
  const guidance = createAssistantGuidance(guidanceApi({ suggestions: () => Promise.reject(new TypeError("bug")) }), await setup());
  await expect(guidance.refresh()).rejects.toBeInstanceOf(TypeError);
  guidance.dispose();
});

it("superseded_failure_cannot_replace_a_newer_success_or_its_loading_state", async () => {
  const pending = deferred<undefined>(); let first = true;
  const guidance = createAssistantGuidance(guidanceApi({ suggestions: () => {
    if (!first) return Promise.resolve(suggestions);
    first = false;
    return pending.promise.then(() => { throw new ApiError(503); });
  } }), await setup());
  const old = guidance.refresh(); await guidance.refresh();
  pending.resolve(undefined); await old;
  expect(guidance.items.value).toEqual(suggestions.items);
  expect(guidance.message.value).toBe(""); expect(guidance.loading.value).toBe(false);
  guidance.dispose();
});
