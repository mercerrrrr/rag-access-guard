import { afterEach, expect, it, vi } from "vitest";

import { requestJson } from "@/api/client";
import { ApiError, errorMessage } from "@/api/errors";
import { csrfSchema, sessionSchema } from "@/api/types";

afterEach(() => vi.unstubAllGlobals());

it.each([
  { status: 429, code: "inference_busy" },
  { status: 503, code: "inference_unavailable" },
  { status: 422, code: "unsupported_structure" },
])("recognizes_closed_inference_error_without_retry", async ({ status, code }) => {
  let calls = 0;
  vi.stubGlobal("fetch", () => {
    calls += 1;
    return Promise.resolve(Response.json({ detail: { code } }, { status, headers: { "Retry-After": "2" } }));
  });
  await expect(requestJson("/api/chat/threads", { method: "POST", body: {}, csrfToken: "csrf", parse: (value) => csrfSchema.parse(value) }))
    .rejects.toMatchObject({ status, code, message: "Request failed" });
  expect(calls).toBe(1);
});

it.each([
  { status: 429, body: { detail: { code: "inference_busy", path: "private" } } },
  { status: 503, body: { detail: { code: "inference_unavailable" }, pid: 123 } },
  { status: 503, body: { detail: { code: "inference_busy" } } },
  { status: 503, body: { detail: { code: "unsupported_structure" } } },
  { status: 422, body: { detail: { code: "unsupported_structure", path: "private" } } },
])("rejects_extra_fields_or_mismatched_inference_status", async ({ status, body }) => {
  vi.stubGlobal("fetch", () => Promise.resolve(Response.json(body, { status })));
  await expect(requestJson("/api/chat/threads", { parse: (value) => csrfSchema.parse(value) }))
    .rejects.toMatchObject({ status, code: null, message: "Request failed" });
});

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

it.each([
  { detail: { code: "unknown", message: "private-text" } },
  { detail: { code: "query_too_long", message: "private-text" } },
  { detail: { code: "query_too_long" }, query: "private-text" },
  { detail: "private-text" },
])("does_not_promote_unrecognized_422_body_to_public_error_code", async (body) => {
  vi.stubGlobal("fetch", () => Promise.resolve(Response.json(body, { status: 422 })));
  const error: unknown = await requestJson("/api/chat/threads", {
    parse: (value) => csrfSchema.parse(value),
  }).catch((caught: unknown) => caught);
  expect(error).toBeInstanceOf(ApiError);
  if (!(error instanceof ApiError)) throw new Error("Expected sanitized HTTP error");
  expect(error).toMatchObject({ status: 422, code: null, message: "Request failed" });
  expect(errorMessage(error)).not.toContain("private-text");
});

it("keeps_malformed_422_json_as_a_sanitized_http_error", async () => {
  vi.stubGlobal("fetch", () => Promise.resolve(new Response("private-text", { status: 422 })));
  await expect(requestJson("/api/chat/threads", { parse: (value) => csrfSchema.parse(value) }))
    .rejects.toMatchObject({ status: 422, code: null, message: "Request failed" });
});
