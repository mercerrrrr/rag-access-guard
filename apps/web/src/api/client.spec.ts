import { afterEach, expect, it, vi } from "vitest";

import { requestJson } from "@/api/client";
import { csrfSchema, sessionSchema } from "@/api/types";

afterEach(() => vi.unstubAllGlobals());

it("sends_same_origin_cookie_csrf_and_json_without_retry", async () => {
  let sent: Request | undefined;
  vi.stubGlobal("fetch", (request: Request) => {
    sent = request.clone();
    return Promise.resolve(Response.json({ csrf_token: "new" }));
  });
  await requestJson("/api/auth/login", { method: "POST", body: { login: "student", password: "private" },
    csrfToken: "challenge", parse: (value) => csrfSchema.parse(value) });
  expect(sent?.credentials).toBe("same-origin");
  expect(sent?.headers.get("X-CSRF-Token")).toBe("challenge");
  expect(sent?.headers.get("Content-Type")).toBe("application/json");
  expect(await sent?.json()).toEqual({ login: "student", password: "private" });
});

it.each([401, 403, 429, 503])("sanitizes_http_%s_without_response_body_or_retry", async (status) => {
  let count = 0;
  vi.stubGlobal("fetch", () => {
    count += 1;
    return Promise.resolve(Response.json({ detail: "private-password" }, {
      status, headers: { "Retry-After": "60" },
    }));
  });
  await expect(requestJson("/api/auth/me", { parse: (value) => sessionSchema.parse(value) }))
    .rejects.toMatchObject({ name: "ApiError", status, message: "Request failed" });
  expect(count).toBe(1);
});

it("rejects_external_paths_before_transmitting_credentials", async () => {
  let count = 0;
  vi.stubGlobal("fetch", () => { count += 1; return Promise.resolve(Response.json({ csrf_token: "bad" })); });
  await expect(requestJson("https://foreign.example/api/auth/me", { parse: (value) => csrfSchema.parse(value) }))
    .rejects.toMatchObject({ name: "ApiError" });
  expect(count).toBe(0);
});

it("parses_empty_logout_without_json_body", async () => {
  vi.stubGlobal("fetch", () => Promise.resolve(new Response(null, { status: 204 })));
  await expect(requestJson("/api/auth/logout", { method: "POST", csrfToken: "csrf",
    parse: (value) => { expect(value).toBeUndefined(); } })).resolves.toBeUndefined();
});

it("malformed_identity_does_not_become_a_typed_session", async () => {
  vi.stubGlobal("fetch", () => Promise.resolve(Response.json({ user: { is_admin: "true" } })));
  await expect(requestJson("/api/auth/me", { parse: (value) => sessionSchema.parse(value) }))
    .rejects.toMatchObject({ name: "ApiError", status: 0 });
});
