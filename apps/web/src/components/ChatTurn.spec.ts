import { mount } from "@vue/test-utils";
import { expect, it } from "vitest";
import ChatTurn from "@/components/ChatTurn.vue";

it("llm_url_is_plain_text", () => {
  const wrapper = mount(ChatTurn, { props: {
    answer: '<img src=x onerror=alert(1)> https://example.invalid/secret',
    sources: [], userInput: "Что сказано в документе?",
  } });
  expect(wrapper.find("img").exists()).toBe(false);
  expect(wrapper.find("a").exists()).toBe(false);
  expect(wrapper.text()).toContain('<img src=x onerror=alert(1)>');
});
