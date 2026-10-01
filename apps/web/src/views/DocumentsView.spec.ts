import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, expect, it, vi } from "vitest";
import { documentsApi } from "@/api/documents";
import { ApiError } from "@/api/errors";
import { sessionKey } from "@/composables/sessionContext";
import { createSessionState } from "@/composables/useSession";
import { document, version } from "@/test/documents";
import { sessionApi, student } from "@/test/session";
import DocumentsView from "@/views/DocumentsView.vue";

afterEach(() => vi.restoreAllMocks());
async function mountView(admin: boolean) {
  const session = createSessionState(sessionApi({ me: () => Promise.resolve({ user: { ...student, is_admin: admin } }) }));
  await session.refresh();
  const wrapper = mount(DocumentsView, { global: { provide: { [sessionKey]: session } } });
  await flushPromises();
  return { wrapper, session };
}

it("admin_metadata_does_not_render_read_control_without_grant", async () => {
  vi.spyOn(documentsApi, "allowed").mockResolvedValue([]);
  vi.spyOn(documentsApi, "admin").mockResolvedValue([document]);
  vi.spyOn(documentsApi, "versions").mockResolvedValue([version]);
  const { wrapper } = await mountView(true);
  await wrapper.get('[data-action="select-document"]').trigger("click");
  await flushPromises();
  expect(wrapper.text()).toContain(version.content_sha256);
  expect(wrapper.find('[data-action="read-content"]').exists()).toBe(false);
  expect(wrapper.find('input[type="file"]').exists()).toBe(true);
  wrapper.unmount();
});

it("reader_opens_escaped_content_then_refresh_removes_revoked_registry", async () => {
  vi.spyOn(documentsApi, "allowed").mockResolvedValue([{ id: document.id, title: document.title, active_version_id: version.id }]);
  vi.spyOn(documentsApi, "text").mockResolvedValue({ document_id: document.id, document_version_id: version.id, text: "<b>SECRET_54</b>" });
  const { wrapper } = await mountView(false);
  await wrapper.get('[data-action="select-document"]').trigger("click");
  await flushPromises();
  await wrapper.get('[data-action="read-content"]').trigger("click");
  await flushPromises();
  expect(wrapper.get('[data-document-content]').text()).toContain("<b>SECRET_54</b>");
  expect(wrapper.find("b").exists()).toBe(false);
  expect(wrapper.find('input[type="file"]').exists()).toBe(false);
  vi.spyOn(documentsApi, "allowed").mockResolvedValue([]);
  await wrapper.get('[data-action="refresh-documents"]').trigger("click");
  await flushPromises();
  expect(wrapper.html()).not.toContain("SECRET_54");
  expect(wrapper.text()).not.toContain(document.title);
  wrapper.unmount();
});

it("server_upload_error_has_no_raw_response_or_success_row", async () => {
  vi.spyOn(documentsApi, "allowed").mockResolvedValue([]);
  vi.spyOn(documentsApi, "admin").mockResolvedValue([]);
  vi.spyOn(documentsApi, "upload").mockRejectedValue(new ApiError(415));
  const { wrapper } = await mountView(true);
  await wrapper.get('input[type="text"]').setValue("New");
  const input = wrapper.get('input[type="file"]');
  Object.defineProperty(input.element, "files", { value: { item: () => new File(["x"], "x.txt", { type: "text/plain" }) } });
  await input.trigger("change");
  await wrapper.get("form").trigger("submit");
  await flushPromises();
  expect(wrapper.get('[role="alert"]').text()).toContain("Тип файла");
  expect(wrapper.find('[data-action="select-document"]').exists()).toBe(false);
  wrapper.unmount();
});
