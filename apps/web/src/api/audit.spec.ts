// @vitest-environment node
import { afterAll, beforeEach, expect, it, vi } from "vitest";
import { auditApi, parseAuditEvent } from "@/api/audit";
import { auditEvent } from "@/test/audit";
beforeEach(() => {
  vi.stubGlobal("window", { location: { origin: "http://localhost" } });
  vi.stubGlobal("document", { body: { innerHTML: "" } });
});
afterAll(() => vi.unstubAllGlobals());
it.each([{ id: "bad" }, { event_type: "arbitrary" }, { stage: "secret" }, { outcome: "unknown" },
  { occurred_at: "2026-09-22T12:00:00" }, { source_count: -1 }, { policy_revision: 1.5 }])(
  "rejects_invalid_audit_row_%j", patch => { expect(() => parseAuditEvent({ ...auditEvent, ...patch })).toThrow(); },
);
it("encodes_closed_filters_and_opaque_cursor_without_publishing_unknown_fields", async () => {
  let sent: Request | undefined;
  vi.stubGlobal("fetch", (request: Request) => { sent = request; return Promise.resolve(Response.json({
    items: [{ ...auditEvent, prompt: "PRIVATE" }], next_cursor: "opaque+cursor/=", raw: "PRIVATE",
  })); });
  const result = await auditApi.list({ event_type: "grant_removed", stage: "policy", outcome: "success",
    since: "2026-09-22T12:00:00+03:00", until: "2026-09-23T12:00:00Z", limit: 25 }, "opaque+cursor/=", new AbortController().signal);
  expect(sent?.method).toBe("GET"); expect(sent?.credentials).toBe("same-origin"); expect(sent?.cache).toBe("no-store");
  const url = new URL(sent?.url ?? "http://missing");
  expect(url.pathname).toBe("/api/admin/audit");
  expect(Object.fromEntries(url.searchParams)).toEqual({ event_type: "grant_removed", stage: "policy", outcome: "success",
    since: "2026-09-22T12:00:00+03:00", until: "2026-09-23T12:00:00Z", limit: "25", cursor: "opaque+cursor/=" });
  expect(result.items).toEqual([auditEvent]); expect(JSON.stringify(result)).not.toContain("PRIVATE");
});
it("invalid_time_range_is_not_submitted", async () => {
  const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
  await expect(auditApi.list({ since: "2026-09-23T00:00:00Z", until: "2026-09-22T00:00:00Z" }, null,
    new AbortController().signal)).rejects.toMatchObject({ status: 422 });
  expect(fetch).not.toHaveBeenCalled();
});
