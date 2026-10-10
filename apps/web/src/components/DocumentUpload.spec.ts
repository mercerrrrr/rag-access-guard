import { mount } from "@vue/test-utils";
import { expect, it } from "vitest";
import DocumentUpload from "@/components/DocumentUpload.vue";

it.each(["application/octet-stream", "text/html"])("unexpected_mime_is_rejected (%s)", async (type) => {
  const wrapper = mount(DocumentUpload, { props: { busy: false } });
  await wrapper.get('input[type="text"]').setValue("Регламент");
  const input = wrapper.get('input[type="file"]');
  Object.defineProperty(input.element, "files", { value: { item: () => new File(["binary"], "file.txt", { type }) } });
  await input.trigger("change");
  await wrapper.get("form").trigger("submit");
  expect(wrapper.emitted("upload")).toBeUndefined();
  expect(wrapper.find('[role="alert"]').exists()).toBe(true);
});

it("valid_text_upload_emits_file_and_trimmed_title", async () => {
  const wrapper = mount(DocumentUpload, { props: { busy: false } });
  await wrapper.get('input[type="text"]').setValue(" Регламент ");
  const file = new File(["text"], "file.txt", { type: "text/plain" });
  const input = wrapper.get('input[type="file"]');
  Object.defineProperty(input.element, "files", { value: { item: () => file } });
  await input.trigger("change");
  await wrapper.get("form").trigger("submit");
  expect(wrapper.emitted("upload")).toEqual([[{ title: "Регламент", file }]]);
});

it("oversized_file_is_rejected_before_upload", async () => {
  const wrapper = mount(DocumentUpload, { props: { busy: false, version: true } });
  const input = wrapper.get('input[type="file"]');
  Object.defineProperty(input.element, "files", { value: { item: () => new File([new Uint8Array(10485761)], "file.txt", { type: "text/plain" }) } });
  await input.trigger("change");
  await wrapper.get("form").trigger("submit");
  expect(wrapper.emitted("upload")).toBeUndefined();
  expect(wrapper.find('[role="alert"]').exists()).toBe(true);
});

it.each(["application/vnd.openxmlformats-officedocument.wordprocessingml.document", ""])("docx_upload_accepts_declared_or_inferred_mime (%s)", async (type) => {
  const wrapper = mount(DocumentUpload, { props: { busy: false, version: true } });
  const input = wrapper.get('input[type="file"]');
  const file = new File(["synthetic"], "FILE.DOCX", { type });
  Object.defineProperty(input.element, "files", { value: { item: () => file } });
  await input.trigger("change");
  await wrapper.get("form").trigger("submit");
  const uploaded = wrapper.emitted<{ title: string; file: File }[]>("upload")?.[0]?.[0];
  expect(uploaded?.file.type).toBe("application/vnd.openxmlformats-officedocument.wordprocessingml.document");
  expect(uploaded?.file.name).toBe("FILE.DOCX");
  expect(wrapper.emitted("upload")).toHaveLength(1);
});
