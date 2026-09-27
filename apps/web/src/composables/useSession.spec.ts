import { expect, it } from "vitest";

import type { SessionApi, SessionDto } from "@/api/types";
import { createSessionState } from "@/composables/useSession";
import { ApiError } from "@/api/errors";
import { deferred, sessionApi, student } from "@/test/session";

it("late_me_cannot_restore_logged_out_session", async () => {
  const pending = deferred<SessionDto>();
  const user = { id: "00000000-0000-4000-8000-000000000001", login: "student",
    display_name: "Студент", is_admin: false };
  const api: SessionApi = {
    me: () => pending.promise,
    csrf: () => Promise.resolve({ csrf_token: "challenge" }),
    login: () => Promise.resolve({ user, csrf_token: "session-csrf" }),
    logout: () => Promise.resolve(),
  };
  const state = createSessionState(api);
  const refresh = state.refresh();
  await state.signOut();
  pending.resolve({ user });
  await refresh;
  expect(state.status.value).toBe("anonymous");
  expect(state.user.value).toBeNull();
});

it("unauthorized_clears_all_protected_state", async () => {
  let identityExists = true;
  const user = { id: "00000000-0000-4000-8000-000000000001", login: "student",
    display_name: "Студент", is_admin: false };
  const api: SessionApi = {
    me: () => identityExists ? Promise.resolve({ user }) : Promise.reject(new ApiError(401)),
    csrf: () => Promise.resolve({ csrf_token: "csrf" }),
    login: () => Promise.resolve({ user, csrf_token: "csrf" }),
    logout: () => Promise.resolve(),
  };
  const state = createSessionState(api);
  await state.refresh();
  identityExists = false;
  await state.refresh();
  expect(state.user.value).toBeNull();
  expect(state.status.value).toBe("anonymous");
});

it("failed_logout_hides_identity_without_claiming_server_revocation", async () => {
  const user = { id: "00000000-0000-4000-8000-000000000001", login: "student",
    display_name: "Студент", is_admin: false };
  const api: SessionApi = {
    me: () => Promise.resolve({ user }),
    csrf: () => Promise.resolve({ csrf_token: "csrf" }),
    login: () => Promise.resolve({ user, csrf_token: "csrf" }),
    logout: () => Promise.reject(new ApiError(503)),
  };
  const state = createSessionState(api);
  await state.refresh();
  await state.signOut();
  expect(state.user.value).toBeNull();
  expect(state.status.value).toBe("unavailable");
});

it("initial_anonymous_bootstrap_does_not_claim_session_expiry", async () => {
  const state = createSessionState(sessionApi({ me: () => Promise.reject(new ApiError(401)) }));
  await state.refresh();
  expect(state.status.value).toBe("anonymous");
  expect(state.message.value).toBe("");
});

it("revalidation_reports_loss_of_an_authenticated_session", async () => {
  let expired = false;
  const state = createSessionState(sessionApi({
    me: () => expired ? Promise.reject(new ApiError(401)) : Promise.resolve({ user: student }),
  }));
  await state.refresh();
  expired = true;
  await state.refresh();
  expect(state.status.value).toBe("anonymous");
  expect(state.message.value).toContain("Сессия завершена");
});
