import { mount } from "@vue/test-utils";
import { expect, it } from "vitest";
import AuditFilters from "@/components/AuditFilters.vue";
it.each([
  ["2026-09-23T12:00:00Z", "2026-09-22T12:00:00Z"],
  ["2026-09-22T12:00:00Z", "2026-09-22T12:00:00Z"],
  ["bad time", ""], ["2026-09-22T12:00:00", ""],
])("invalid_time_range_is_not_submitted_%s_%s", async (since, until) => {
  const wrapper = mount(AuditFilters);
  await wrapper.get('[name="since"]').setValue(since); await wrapper.get('[name="until"]').setValue(until);
  await wrapper.get("form").trigger("submit");
  expect(wrapper.emitted("submit")).toBeUndefined(); expect(wrapper.find('[role="alert"]').exists()).toBe(true);
});
it("valid_aware_filters_are_submitted_and_reset_is_explicit", async () => {
  const wrapper = mount(AuditFilters, { attachTo: document.body });
  await wrapper.get('[name="since"]').setValue("2026-09-22T12:00:00+03:00");
  await wrapper.get("form").trigger("submit");
  expect(wrapper.emitted("submit")?.[0]).toEqual([{ since: "2026-09-22T12:00:00+03:00", limit: 50 }]);
  await wrapper.get('button[type="reset"]').trigger("click");
  expect(wrapper.emitted("submit")?.[1]).toEqual([{}]);
  wrapper.unmount();
});
