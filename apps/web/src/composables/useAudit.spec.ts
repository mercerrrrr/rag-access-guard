import { expect, it, vi } from "vitest";
import { createAuditState } from "@/composables/useAudit";
import { createSessionState } from "@/composables/useSession";
import type { AuditApi, AuditPage } from "@/api/audit";
import { auditEvent } from "@/test/audit";
import { deferred, sessionApi, student } from "@/test/session";
import { ApiError } from "@/api/errors";
async function setup(api: AuditApi, admin = true) {
  const session = createSessionState(sessionApi({ me: () => Promise.resolve({ user: { ...student, is_admin: admin } }) }));
  await session.refresh(); return { session, state: createAuditState(api, session) };
}
const first: AuditPage = { items: [auditEvent], next_cursor: "opaque" };
it("loads_pages_with_the_same_filters_and_opaque_cursor", async () => {
  const second = { ...auditEvent, id: "00000000-0000-4000-8000-000000000005" };
  const list = vi.fn<AuditApi["list"]>().mockResolvedValueOnce(first).mockResolvedValueOnce({ items: [second], next_cursor: null });
  const { state } = await setup({ list });
  await state.load({ stage: "policy" }); await state.loadMore();
  expect(list).toHaveBeenNthCalledWith(2, { stage: "policy" }, "opaque", expect.any(AbortSignal));
  expect(state.items.value.map(row => row.id)).toEqual([auditEvent.id, second.id]);
  expect(state.nextCursor.value).toBeNull(); state.dispose();
});
it("filter_response_order_does_not_restore_old_rows", async () => {
  const old = deferred<AuditPage>(), fresh = deferred<AuditPage>();
  const list = vi.fn<AuditApi["list"]>().mockResolvedValueOnce(first).mockImplementationOnce(() => old.promise).mockImplementationOnce(() => fresh.promise);
  const { state } = await setup({ list }); await state.load({});
  const oldLoad = state.loadMore(); const newLoad = state.load({ outcome: "denied" });
  expect(state.items.value).toEqual([]); expect(state.nextCursor.value).toBeNull();
  fresh.resolve({ items: [{ ...auditEvent, outcome: "denied" }], next_cursor: null }); await newLoad;
  old.resolve(first); await oldLoad;
  expect(state.items.value.map(row => row.outcome)).toEqual(["denied"]); state.dispose();
});
it("logout_clears_audit_ids_and_rejects_late_pages", async () => {
  const late = deferred<AuditPage>();
  const list = vi.fn<AuditApi["list"]>().mockResolvedValueOnce(first).mockImplementationOnce(() => late.promise);
  const { state, session } = await setup({ list }); await state.load({});
  expect(state.items.value).toHaveLength(1);
  const loading = state.loadMore(); await session.signOut();
  expect(state.items.value).toEqual([]); expect(state.nextCursor.value).toBeNull();
  late.resolve(first); await loading; expect(state.items.value).toEqual([]); state.dispose();
});
it("non_admin_cannot_load_audit", async () => {
  const list = vi.fn<AuditApi["list"]>().mockResolvedValue(first);
  const { state } = await setup({ list }, false); await state.load({}); await state.loadMore();
  expect(list).not.toHaveBeenCalled(); expect(state.items.value).toEqual([]); state.dispose();
});
it("failed_page_keeps_confirmed_rows_and_cursor_without_raw_error", async () => {
  const list = vi.fn<AuditApi["list"]>().mockResolvedValueOnce(first).mockRejectedValueOnce(new ApiError(503));
  const { state } = await setup({ list }); await state.load({}); await state.loadMore();
  expect(state.items.value).toEqual([auditEvent]); expect(state.nextCursor.value).toBe("opaque");
  expect(state.message.value).not.toBe(""); state.dispose();
});
it("admin_denial_suspends_session_and_clears_rows", async () => {
  const list = vi.fn<AuditApi["list"]>().mockResolvedValueOnce(first).mockRejectedValueOnce(new ApiError(403));
  const { state, session } = await setup({ list }); await state.load({}); await state.loadMore();
  expect(session.protectedBlocked.value).toBe(true); expect(state.items.value).toEqual([]); state.dispose();
});
