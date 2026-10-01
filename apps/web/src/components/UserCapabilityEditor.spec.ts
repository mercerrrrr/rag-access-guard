import { mount } from "@vue/test-utils";
import { expect, it } from "vitest";
import UserCapabilityEditor from "@/components/UserCapabilityEditor.vue";
import { accessUser } from "@/test/access";
it("user_capability_changes_require_explicit_confirmation", async () => {
  const wrapper = mount(UserCapabilityEditor, { props: { user: accessUser } });
  await wrapper.get('input[name="is_admin"]').setValue(true);
  await wrapper.get("form").trigger("submit");
  expect(wrapper.emitted("save")).toBeUndefined();
  expect(wrapper.text()).toContain("Читатель");
  await wrapper.get('[data-action="confirm-user"]').trigger("click");
  expect(wrapper.emitted("save")).toEqual([[{ is_active: true, is_admin: true }]]);
});
