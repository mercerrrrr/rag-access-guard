import { z } from "zod";
import { test, expect } from "./fixtures";
import { adminDocumentSchema } from "../src/api/documentTypes";
import { grantSchema } from "../src/api/accessTypes";
import { messageResponseSchema } from "../src/api/types";

const question = "Как подать учебную заявку?";
const help = "Я помогаю искать ответы в доступных документах. Задайте конкретный вопрос или выберите пример. Специальные команды не нужны. Источники ответа можно открыть в разделе «Источники ответа».";

test("guidance_is_authorized_and_choice_only_fills_the_draft", async ({ actors }) => {
  const page = actors.teacher.page;
  const choice = page.getByRole("button", { name: question, exact: true });
  await expect(choice).toBeVisible();
  const disabled = process.env["RAG_E2E_DISABLE_MODEL"] === "1";
  const modelState = disabled ? "Модель недоступна" : "Тестовый режим: ответы модели отключены";
  await expect(page.getByText(modelState, { exact: true })).toBeVisible();
  const input = page.getByLabel("Вопрос по документам", { exact: true });
  for (const [width, height] of [[1600, 900], [1280, 900], [768, 900], [375, 900], [800, 450]] as const) {
    await page.setViewportSize({ width, height });
    await page.emulateMedia({ reducedMotion: "reduce" });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: test.info().outputPath(`welcome-${String(width)}-${disabled ? 'disabled' : 'test'}.png`), fullPage: true });
    if (width === 768) {
      const send = page.getByRole("button", { name: "Отправить", exact: true });
      await send.scrollIntoViewIfNeeded();
      await expect(send).toBeVisible();
      await input.fill("Проверка клавиатуры");
      await input.focus();
      await page.keyboard.press("Tab");
      await expect(send).toBeFocused();
      await expect(send).toBeEnabled();
      await page.screenshot({ path: test.info().outputPath("composer-768-reachable.png"), fullPage: true });
      await input.fill("");
    }
  }
  await page.setViewportSize({ width: 1600, height: 900 });
  let sent = 0;
  page.on("request", request => { if (request.method() === "POST" && request.url().endsWith("/messages")) sent += 1; });
  await choice.click();
  await expect(input).toHaveValue(question);
  await expect(input).toBeFocused();
  expect(sent).toBe(0);
  await input.fill("Привет!");
  const answer = page.waitForResponse(response => response.url().endsWith("/messages"));
  await input.press("Enter");
  const response = await answer;
  expect(response.status()).toBe(200);
  expect(response.headers()["cache-control"]).toBe("private, no-store");
  expect(messageResponseSchema.parse(await response.json()).turn).toMatchObject({
    state: "neutral", answer: null, sources: [], message: help,
  });
  await expect(page.getByText(help, { exact: true })).toBeVisible();
  await page.screenshot({ path: test.info().outputPath("local-help.png"), fullPage: true });
  await expect(actors.student.page.getByText(modelState, { exact: true })).toBeVisible();
  await expect(actors.student.page.getByRole("button", { name: question, exact: true })).toHaveCount(0);
  await actors.student.page.screenshot({ path: test.info().outputPath("welcome-no-suggestions.png"), fullPage: true });
});

test("generic_shell_survives_protected_sections_and_narrow_width", async ({ actors }) => {
  const page = actors.admin.page;
  for (const [route, label, refresh] of [
    ["/documents", "Документы", "Обновить реестр"],
    ["/access", "Доступ", "Обновить данные"],
    ["/audit", "Аудит", "Обновить журнал"],
  ] as const) {
    await page.getByRole("link", { name: label, exact: true }).click();
    await expect(page.getByRole("heading", { name: label, exact: true, level: 1 })).toBeVisible();
    await expect(page.getByRole("button", { name: refresh, exact: true })).toBeEnabled();
    await expect(page.getByRole("alert")).toHaveCount(0);
    if (route === "/audit") await expect(page.getByText(/^Загружено событий:/)).toBeVisible();
    await expect(page.getByText("Помощник по базе знаний", { exact: true })).toBeVisible();
    for (const width of [1600, 375]) {
      await page.setViewportSize({ width, height: 900 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      await page.screenshot({ path: test.info().outputPath(`${route.slice(1)}-${String(width)}.png`), fullPage: true });
    }
  }
  await page.setViewportSize({ width: 1600, height: 900 });
});

test("a_revoked_suggestion_disappears_on_fresh_focus", async ({ actors }) => {
  const response = await actors.admin.context.request.get("/api/admin/documents");
  const documents = z.object({ items: z.array(adminDocumentSchema) }).parse(await response.json());
  const document = documents.items.find(item => item.title === "Synthetic guidance document");
  if (!document) throw new Error("Expected managed guidance fixture");
  const path = `/api/admin/documents/${document.id}/grants`;
  const listed = await actors.admin.context.request.get(path);
  const grants = z.object({ items: z.array(grantSchema) }).parse(await listed.json());
  const grant = grants.items.find(item => item.user_id === actors.teacher.id);
  if (!grant) throw new Error("Expected teacher fixture grant");
  const page = actors.teacher.page;
  await page.getByRole("button", { name: "Новый", exact: true }).click();
  await expect(page.getByRole("button", { name: question, exact: true })).toBeVisible();
  try {
    expect((await actors.admin.context.request.delete(`${path}/${grant.id}`, { headers: actors.admin.headers })).status()).toBe(204);
    const refreshed = page.waitForResponse(item => item.url().endsWith("/api/chat/suggestions"));
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    const suggestions = await refreshed;
    expect(suggestions.status()).toBe(200);
    expect(suggestions.headers()["cache-control"]).toBe("private, no-store");
    await expect(page.getByRole("button", { name: question, exact: true })).toHaveCount(0);
    expect(JSON.stringify(await suggestions.json())).not.toContain(question);
  } finally {
    expect((await actors.admin.context.request.post(path, {
      headers: actors.admin.headers, data: { user_id: actors.teacher.id },
    })).status()).toBe(201);
  }
});
