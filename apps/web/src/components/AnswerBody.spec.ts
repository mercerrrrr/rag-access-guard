import { mount } from "@vue/test-utils";
import { expect, it } from "vitest";
import AnswerBody from "./AnswerBody.vue";

it("shows semantic lists without accepting model links", () => {
  const wrapper = mount(AnswerBody, { props: {
    text: "1. **Подготовьте заявку**\n2. Откройте [ссылку](javascript:alert(1))",
  } });
  expect(wrapper.findAll("li")).toHaveLength(2);
  expect(wrapper.get("strong").text()).toBe("Подготовьте заявку");
  expect(wrapper.find("a").exists()).toBe(false);
  wrapper.unmount();
});

it.each([
  '<img src=x onerror=alert(1)>',
  '<script>alert(1)</script>',
  '<svg onload=alert(1)><a href="javascript:alert(1)">x</a></svg>',
  '<iframe src="data:text/html,secret"></iframe>',
  '![образец](data:image/svg+xml,secret)',
  '[ссылка](javascript:alert(1))',
  '<https://example.invalid/source>',
  'https://example.invalid/source [1]',
  '[1]: https://example.invalid/source',
])("renders hostile model content as visible inactive text: %s", text => {
  const wrapper = mount(AnswerBody, { props: { text } });
  expect(wrapper.find("img, a, script, iframe, svg, button").exists()).toBe(false);
  expect(wrapper.text()).toBe(text);
  for (const element of wrapper.findAll("*")) {
    expect(Object.keys(element.attributes()).some(name => name.startsWith("on"))).toBe(false);
  }
  wrapper.unmount();
});

it("decoded entities and fenced HTML never become active elements", () => {
  const wrapper = mount(AnswerBody, { props: {
    text: '&lt;img src=x onerror=alert(1)&gt;\n\n```html\n<script>код</script>\n```',
  } });
  expect(wrapper.find("img, script").exists()).toBe(false);
  expect(wrapper.text()).toContain('<img src=x onerror=alert(1)>');
  expect(wrapper.get("pre code").text()).toContain('<script>код</script>');
  wrapper.unmount();
});
