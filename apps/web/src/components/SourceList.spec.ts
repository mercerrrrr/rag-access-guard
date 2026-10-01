import { mount } from "@vue/test-utils";
import { expect, it } from "vitest";
import SourceList from "@/components/SourceList.vue";
import { source } from "@/test/chat";

it("source_selection_uses_a_native_button_and_server_witness", async () => {
  const wrapper = mount(SourceList, { props: { sources: [source], selected: source.chunk_id } });
  expect(wrapper.find("button").exists()).toBe(true);
  const button = wrapper.get("button");
  expect(button.attributes("aria-pressed")).toBe("true");
  await button.trigger("click");
  expect(wrapper.emitted("select")).toEqual([[source]]);
  expect(wrapper.find("a").exists()).toBe(false);
});
