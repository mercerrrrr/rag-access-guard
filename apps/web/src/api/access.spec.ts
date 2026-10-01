// @vitest-environment node
import { afterAll, beforeEach, expect, it, vi } from "vitest";
import { accessApi } from "@/api/access";
const doc = "00000000-0000-4000-8000-000000000055";
const user = "00000000-0000-4000-8000-000000000056";
const role = "00000000-0000-4000-8000-000000000057";
const id = "00000000-0000-4000-8000-000000000058";
const grant = { id, document_id: doc, user_id: user, role_id: null };
const signal = () => new AbortController().signal;
beforeEach(() => {
  vi.stubGlobal("window", { location: { origin: "http://localhost" } });
  vi.stubGlobal("document", { body: { innerHTML: "" } });
});
afterAll(() => vi.unstubAllGlobals());

it("sends_one_grant_target_with_session_and_csrf", async () => {
  let sent: Request | undefined;
  vi.stubGlobal("fetch", async (request: Request) => {
    sent = request.clone(); await request.arrayBuffer(); return Response.json(grant, { status: 201 });
  });
  await expect(accessApi.createGrant(doc, { user_id: user }, "csrf", signal())).resolves.toEqual(grant);
  expect(sent?.credentials).toBe("same-origin");
  expect(sent?.headers.get("X-CSRF-Token")).toBe("csrf");
  expect(await sent?.json()).toEqual({ user_id: user });
});
it("rejects_both_targets_before_network", async () => {
  const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
  await expect(accessApi.createGrant(doc, { user_id: user, role_id: role }, "csrf", signal())).rejects.toBeDefined();
  expect(fetch).not.toHaveBeenCalled();
});
it("rejects_grant_rows_bound_to_another_document", async () => {
  vi.stubGlobal("fetch", () => Promise.resolve(Response.json({ items: [{ ...grant, document_id: role }] })));
  await expect(accessApi.grants(doc, signal())).rejects.toMatchObject({ status: 0 });
});
it("rejects_mismatched_created_subject", async () => {
  vi.stubGlobal("fetch", async (request: Request) => { await request.arrayBuffer(); return Response.json(grant); });
  await expect(accessApi.createGrant(doc, { role_id: role }, "csrf", signal())).rejects.toMatchObject({ status: 0 });
});
it("supports_204_membership_and_revoke_without_retry", async () => {
  const methods: string[] = [];
  vi.stubGlobal("fetch", (request: Request) => { methods.push(request.method); return Promise.resolve(new Response(null, { status: 204 })); });
  await accessApi.addMember(role, user, "csrf", signal());
  await accessApi.removeMember(role, user, "csrf", signal());
  await accessApi.revokeGrant(doc, id, "csrf", signal());
  expect(methods).toEqual(["PUT", "DELETE", "DELETE"]);
});
it("duplicate_grant_preserves_sanitized_server_conflict", async () => {
  const fetch = vi.fn(async (request: Request) => { await request.arrayBuffer(); return Response.json({ detail: "PRIVATE" }, { status: 409 }); });
  vi.stubGlobal("fetch", fetch);
  await expect(accessApi.createGrant(doc, { user_id: user }, "csrf", signal())).rejects.toMatchObject({ status: 409, message: "Request failed" });
  expect(fetch).toHaveBeenCalledTimes(1);
});
