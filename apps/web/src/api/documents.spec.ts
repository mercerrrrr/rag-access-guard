// @vitest-environment node
import { afterAll, beforeEach, expect, it, vi } from "vitest";
import { documentsApi } from "@/api/documents";

const id = "00000000-0000-4000-8000-000000000054";
const versionId = "00000000-0000-4000-8000-000000000055";
const document = { id, title: "Регламент", active_version_id: versionId,
  is_active: true, created_at: "2026-10-01T00:00:00Z" };
const signal = () => new AbortController().signal;
beforeEach(() => {
  vi.stubGlobal("window", { location: { origin: "http://localhost" } });
  vi.stubGlobal("document", { body: { innerHTML: "" } });
});
afterAll(() => vi.unstubAllGlobals());

it("uploads_multipart_with_cookie_csrf_and_browser_boundary", async () => {
  let sent: Request | undefined;
  vi.stubGlobal("fetch", async (request: Request) => {
    sent = request.clone();
    await request.arrayBuffer();
    return Response.json(document, { status: 201 });
  });
  const result = await documentsApi.upload({ title: "Регламент",
    file: new File(["synthetic"], "rules.txt", { type: "text/plain" }) }, "csrf", signal());
  expect(result).toEqual(document);
  expect(sent?.credentials).toBe("same-origin");
  expect(sent?.headers.get("X-CSRF-Token")).toBe("csrf");
  expect(sent?.headers.get("Content-Type")).toContain("multipart/form-data; boundary=");
  const form = await sent?.formData();
  expect(form?.get("title")).toBe("Регламент");
  expect(form?.get("file")).toHaveProperty("name", "rules.txt");
});

it("parses_allowed_registry_without_administrative_privilege", async () => {
  const allowed = { id, title: document.title, active_version_id: versionId };
  vi.stubGlobal("fetch", () => Promise.resolve(Response.json({ items: [allowed] })));
  await expect(documentsApi.allowed(signal())).resolves.toEqual([allowed]);
});

it("rejects_mismatched_text_identity", async () => {
  vi.stubGlobal("fetch", () => Promise.resolve(Response.json({ document_id: versionId,
    document_version_id: versionId, text: "PRIVATE" })));
  await expect(documentsApi.text({ documentId: id, versionId }, signal())).rejects.toMatchObject({ status: 0 });
});

it.each([413, 415, 422, 503])("failed_upload_%s_is_not_a_ready_document", async (status) => {
  let calls = 0;
  vi.stubGlobal("fetch", async (request: Request) => {
    calls += 1;
    await request.arrayBuffer();
    return Response.json({ detail: "PRIVATE" }, { status });
  });
  await expect(documentsApi.upload({ title: "Test", file: new File(["test"], "test.txt") }, "csrf", signal()))
    .rejects.toMatchObject({ status, message: "Request failed" });
  expect(calls).toBe(1);
});

it("rejects_versions_belonging_to_another_document", async () => {
  vi.stubGlobal("fetch", () => Promise.resolve(Response.json({ items: [{ id: versionId,
    document_id: versionId, status: "ready", created_at: "2026-10-01T00:00:00Z",
    byte_size: 1, content_sha256: "0".repeat(64) }] })));
  await expect(documentsApi.versions(id, signal())).rejects.toMatchObject({ status: 0 });
});
