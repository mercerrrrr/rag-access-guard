import { mount } from "@vue/test-utils";
import { expect, it } from "vitest";
import RequestProgress from "@/components/RequestProgress.vue";
import type { ProgressPhase } from "@/composables/useRequestProgress";

const phases: readonly ProgressPhase[] = ["sending", "retrieval", "generation", "authorization", "recovery",
  "terminal", "unavailable", "expired", "stopped"];
it.each(phases)("presents %s as a polite textual status independent of elapsed ticks", async phase => {
  const wrapper = mount(RequestProgress, { props: { phase, elapsed: 7 } });
  const status = wrapper.get('[role="status"]'); const label = status.text();
  expect(label.length).toBeGreaterThan(0); expect(status.attributes("aria-live")).toBe("polite");
  expect(status.find(".request-progress__elapsed").exists()).toBe(false);
  await wrapper.setProps({ elapsed: 8 });
  expect(status.text()).toBe(label); expect(wrapper.get(".request-progress__elapsed").text()).toContain("8 с");
  expect(wrapper.find('[role="progressbar"]').exists()).toBe(false); wrapper.unmount();
});
