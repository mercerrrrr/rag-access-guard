import { mount } from "@vue/test-utils";
import { expect, it } from "vitest";
import ChatTurn from "@/components/ChatTurn.vue";
import { reply } from "@/test/chat";
import type { TurnView } from "@/api/types";
import { HELP_MESSAGE } from "@/api/types";

it("llm_url_is_plain_text", () => {
  const wrapper = mount(ChatTurn, { props: {
    turn: { ...reply.turn, state: "available", message: null,
      answer: '<img src=x onerror=alert(1)> https://example.invalid/secret',
      user_input: "Что сказано в документе?" },
  } });
  expect(wrapper.find("img").exists()).toBe(false);
  expect(wrapper.find("a").exists()).toBe(false);
  expect(wrapper.text()).toContain('<img src=x onerror=alert(1)>');
});

it("available_answer_has_semantic_lists_without_model_links", async () => {
  const wrapper = mount(ChatTurn, { props: { turn: { ...reply.turn,
    state: "available", message: null, user_input: "**Вопрос**",
    answer: "1. **Подготовьте заявку**\n2. Откройте [ссылку](javascript:alert(1))\n\n`версия`",
  } } });
  const answer = wrapper.get('[aria-label="Ответ"]');
  expect(answer.findAll("li")).toHaveLength(2);
  expect(answer.get("strong").text()).toBe("Подготовьте заявку");
  expect(answer.get("code").text()).toBe("версия");
  expect(answer.find("a").exists()).toBe(false);
  expect(wrapper.get('[aria-label="Вопрос"]').find("strong").exists()).toBe(false);
  await wrapper.setProps({ turn: { ...reply.turn, state: "unavailable", answer: null, sources: [],
    message: "Ответ недоступен: права на один из источников изменились." } });
  expect(wrapper.find("li, strong, code").exists()).toBe(false);
  expect(wrapper.html()).not.toContain("Подготовьте заявку");
  expect(wrapper.find("button").exists()).toBe(false);
  wrapper.unmount();
});

it.each([
  { ...reply.turn, state: "pending", answer: null, sources: [], message: null },
  { ...reply.turn, state: "neutral", answer: null, sources: [], message: "Нет доступных источников для ответа." },
  { ...reply.turn, state: "neutral", answer: null, sources: [], message: HELP_MESSAGE },
] satisfies readonly TurnView[])("renders $state without an answer-source action", turn => {
  const wrapper = mount(ChatTurn, { props: { turn } });
  expect(wrapper.get('[aria-label="Ответ"]').text()).not.toContain("Защищённый ответ");
  expect(wrapper.find("button").exists()).toBe(false);
  expect(wrapper.find('[role="status"]').exists()).toBe(false);
  expect(wrapper.get('[aria-label="Ответ"] p').text().length).toBeGreaterThan(0);
  wrapper.unmount();
});

it("replacing_an_available_turn_unmounts_answer_and_source_controls", async () => {
  const wrapper = mount(ChatTurn, { props: { turn: reply.turn, selected: true } });
  expect(wrapper.text()).toContain("Защищённый ответ");
  expect(wrapper.get("button").attributes("aria-pressed")).toBe("true");

  await wrapper.setProps({ turn: { ...reply.turn, state: "unavailable", answer: null, sources: [],
    message: "Ответ недоступен: права на один из источников изменились." } });

  expect(wrapper.get('[aria-label="Вопрос"]').text()).toContain("Вопрос");
  expect(wrapper.html()).not.toContain("Защищённый ответ");
  expect(wrapper.find("button").exists()).toBe(false);
  expect(wrapper.get('[role="status"]').text()).toContain("Ответ недоступен");
  wrapper.unmount();
});
