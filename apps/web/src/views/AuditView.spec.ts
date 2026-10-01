import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, expect, it, vi } from "vitest";
import AuditView from "@/views/AuditView.vue";
import { auditApi } from "@/api/audit";
import { ApiError } from "@/api/errors";
import { auditEvent } from "@/test/audit";
import { sessionKey } from "@/composables/sessionContext";
import { createSessionState } from "@/composables/useSession";
import { sessionApi, student } from "@/test/session";
afterEach(() => vi.restoreAllMocks());
async function mountView(admin = true) {
  const session = createSessionState(sessionApi({ me: () => Promise.resolve({ user: { ...student, is_admin: admin } }) }));
  await session.refresh();
  const wrapper = mount(AuditView, { global: { provide: { [sessionKey]: session } } });
  await flushPromises(); return { wrapper, session };
}
it("loads_live_audit_and_clears_identifiers_on_logout", async () => {
  vi.spyOn(auditApi, "list").mockResolvedValue({ items: [auditEvent], next_cursor: null });
  const { wrapper, session } = await mountView(); expect(wrapper.text()).toContain(auditEvent.id);
  await session.signOut(); await flushPromises(); expect(wrapper.text()).not.toContain(auditEvent.id);
  expect(wrapper.find("table").exists()).toBe(false); wrapper.unmount();
});
it("failed_load_is_not_rendered_as_an_empty_success", async () => {
  vi.spyOn(auditApi, "list").mockRejectedValue(new ApiError(503));
  const { wrapper } = await mountView(); expect(wrapper.find('[role="alert"]').exists()).toBe(true);
  expect(wrapper.text()).not.toContain("События не найдены"); wrapper.unmount();
});
it("non_admin_view_does_not_request_audit", async () => {
  const list = vi.spyOn(auditApi, "list").mockResolvedValue({ items: [auditEvent], next_cursor: null });
  const { wrapper } = await mountView(false); expect(list).not.toHaveBeenCalled();
  expect(wrapper.find("form").exists()).toBe(false); wrapper.unmount();
});
