import { mount } from "@vue/test-utils";
import { expect, it } from "vitest";
import DocumentVersionList from "@/components/DocumentVersionList.vue";
import { version } from "@/test/documents";

it("admin_without_grant_cannot_open_content", () => {
  const wrapper = mount(DocumentVersionList, {
    props: {
      versions: [version],
      canReadContent: false,
    },
  });
  expect(wrapper.find('[data-action="read-content"]').exists()).toBe(false);
  expect(wrapper.emitted("open-content")).toBeUndefined();
  expect(wrapper.text()).toContain("Готово");
});

it("read_action_targets_only_allowed_active_version", async () => {
  const old = { ...version, id: "00000000-0000-4000-8000-000000000056" };
  const wrapper = mount(DocumentVersionList, { props: {
    versions: [old, version], canReadContent: true, activeVersionId: version.id,
  } });
  expect(wrapper.findAll('[data-action="read-content"]')).toHaveLength(1);
  await wrapper.get('[data-action="read-content"]').trigger("click");
  expect(wrapper.emitted("open-content")).toEqual([[version.id]]);
  expect(wrapper.text()).toContain(version.content_sha256);
  expect(wrapper.text()).toContain("24");
});
