import { mount } from "@vue/test-utils";
import { expect, it } from "vitest";
import AssistantWelcome from "@/components/AssistantWelcome.vue";
import { profile, suggestions, system } from "@/test/demo";

const props = { items: suggestions.items, profile, system, loading: false, message: "" };
it("explains_the_document_scope_and_emits_only_the_selected_question", async () => {
  const question = "<img src=x onerror=alert(1)>";
  const wrapper = mount(AssistantWelcome, { props: { ...props, items: [{ id: "q", question }] } });
  expect(wrapper.text()).toContain("доступным документам");
  expect(wrapper.text()).toContain("Исследовательский прототип");
  expect(wrapper.text()).toContain("Специальные команды не нужны");
  expect(wrapper.text()).toContain("Источники ответа можно открыть в разделе «Источники ответа».");
  expect(wrapper.text()).not.toContain("открыть справа");
  expect(wrapper.find("img").exists()).toBe(false); expect(wrapper.find("a").exists()).toBe(false);
  await wrapper.get("button").trigger("click");
  expect(wrapper.emitted("choose")).toEqual([[question]]);
});
it.each([
  { status: system, label: "Локальная модель готова" },
  { status: { ...system, mode: "test", model: { name: null, state: "test" } }, label: "Тестовый режим: ответы модели отключены" },
  { status: { ...system, mode: "disabled", model: { name: null, state: "disabled" } }, label: "Модель недоступна" },
  { status: { ...system, model: { name: "audited", state: "unavailable" } }, label: "Модель недоступна" },
] as const)("shows_honest_model_state_$label", ({ status, label }) => {
  const wrapper = mount(AssistantWelcome, { props: { ...props, system: status } });
  expect(wrapper.get('[role="status"]').text()).toContain(label);
});
it("loading_unknown_and_search_unavailable_are_explicit_without_invented_examples", async () => {
  const wrapper = mount(AssistantWelcome, { props: { ...props, items: [], system: null, profile: null, loading: true } });
  expect(wrapper.text()).toContain("Проверяем состояние модели"); expect(wrapper.find("button").exists()).toBe(false);
  await wrapper.setProps({ loading: false, message: "Не удалось загрузить подсказки. Повторите обновление." });
  expect(wrapper.text()).toContain("Состояние модели неизвестно");
  await wrapper.setProps({ system: { ...system, search: { state: "unavailable" } } });
  expect(wrapper.text()).toContain("Поиск по документам недоступен");
});
it("disabled_composer_does_not_offer_an_active_question_action", () => {
  const wrapper = mount(AssistantWelcome, { props: { ...props, disabled: true } });
  expect(wrapper.get<HTMLButtonElement>("button").element.disabled).toBe(true);
});
