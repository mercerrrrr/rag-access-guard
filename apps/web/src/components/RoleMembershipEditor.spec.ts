import { flushPromises, mount } from "@vue/test-utils";
import { expect, it } from "vitest";
import RoleMembershipEditor from "@/components/RoleMembershipEditor.vue";
import { accessRole, accessUser } from "@/test/access";
it("membership_removal_requires_named_confirmation", async () => {
  const wrapper = mount(RoleMembershipEditor, { props: { role: accessRole, users: [accessUser], members: [accessUser] } });
  await wrapper.get("button").trigger("click");
  expect(wrapper.emitted("remove")).toBeUndefined();
  expect(wrapper.text()).toContain("Читатель");
  expect(wrapper.text()).toContain("Читатели");
  await wrapper.get('[data-action="confirm-remove-member"]').trigger("click");
  expect(wrapper.emitted("remove")).toEqual([[accessUser.id]]);
});
it("successful_member_removal_restores_heading_focus", async () => {
  const wrapper = mount(RoleMembershipEditor, { attachTo: document.body, props: { role: accessRole, users: [accessUser], members: [accessUser] } });
  await wrapper.get("button").trigger("click");
  await wrapper.get('[data-action="confirm-remove-member"]').trigger("click");
  await wrapper.setProps({ members: [] }); await flushPromises();
  expect(document.activeElement).toBe(wrapper.get("h3").element);
  expect(wrapper.find('[data-action="confirm-remove-member"]').exists()).toBe(false); wrapper.unmount();
});
