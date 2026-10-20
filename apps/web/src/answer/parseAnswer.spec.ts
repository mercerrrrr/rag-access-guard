import { expect, it } from "vitest";
import { parseAnswer } from "./parseAnswer";

it("preserves paragraphs, line breaks and inline meaning", () => {
  expect(parseAnswer("Первая строка\nвторая *важная* и **точная**, `код`.\n\nАбзац")).toEqual([
    { kind: "paragraph", children: [
      { kind: "text", text: "Первая строка" }, { kind: "text", text: "\n" },
      { kind: "text", text: "вторая " },
      { kind: "emphasis", children: [{ kind: "text", text: "важная" }] },
      { kind: "text", text: " и " },
      { kind: "strong", children: [{ kind: "text", text: "точная" }] },
      { kind: "text", text: ", " }, { kind: "code", text: "код" }, { kind: "text", text: "." },
    ] }, { kind: "paragraph", children: [{ kind: "text", text: "Абзац" }] },
  ]);
});

it("retains ordered start and nested lists", () => {
  expect(parseAnswer("3. Первый\n   - Вложенный\n4. Второй")).toEqual([
    { kind: "list", ordered: true, start: 3, items: [
      [{ kind: "paragraph", children: [{ kind: "text", text: "Первый" }] },
        { kind: "list", ordered: false, start: 1, items: [
          [{ kind: "paragraph", children: [{ kind: "text", text: "Вложенный" }] }],
        ] }], [{ kind: "paragraph", children: [{ kind: "text", text: "Второй" }] }],
    ] },
  ]);
});

it("keeps unsupported headings and tables as text", () => {
  const text = "# Заголовок\n\n| A | B |\n|---|---|\n| 1 | 2 |";
  expect(parseAnswer(text)).toEqual([
    { kind: "paragraph", children: [{ kind: "text", text: "# Заголовок" }] },
    { kind: "paragraph", children: [{ kind: "text", text: "| A | B |" },
      { kind: "text", text: "\n" }, { kind: "text", text: "|---|---|" },
      { kind: "text", text: "\n" }, { kind: "text", text: "| 1 | 2 |" }] },
  ]);
});

it.each([
  "я".repeat(32769),
  Array.from({ length: 40 }, (_, index) => `${"  ".repeat(index)}- Уровень`).join("\n"),
  "*а* ".repeat(4000),
  "-\n".repeat(10001),
  "> Неподдерживаемый блок\n\n**Сохранить весь текст**",
])("falls back to the entire original answer on exceeded or unsupported structure", text => {
  expect(parseAnswer(text)).toEqual([{ kind: "paragraph", children: [{ kind: "text", text }] }]);
});

it("accepts the UTF-8 byte boundary without losing characters", () => {
  const text = "я".repeat(32768);
  expect(parseAnswer(text)).toEqual([{ kind: "paragraph", children: [{ kind: "text", text }] }]);
});

it("handles broken fences and emphasis without dropping text", () => {
  expect(parseAnswer("```русский\n<тег>\nкод")).toEqual([{ kind: "code", text: "<тег>\nкод" }]);
  expect(parseAnswer("*незакрыто")).toEqual([
    { kind: "paragraph", children: [{ kind: "text", text: "*незакрыто" }] },
  ]);
});

it.each([
  "[1]: https://example.invalid/source",
  "[1]: https://example.invalid/source\n\nСохранить весь текст",
])("preserves reference definitions as inactive text", text => {
  const visible = parseAnswer(text).flatMap(block => block.kind === "paragraph"
    ? block.children.flatMap(node => node.kind === "text" ? [node.text] : []) : []).join("\n\n");
  expect(visible).toBe(text);
});
