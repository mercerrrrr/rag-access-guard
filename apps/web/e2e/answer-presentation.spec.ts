import { randomUUID } from "node:crypto";
import type { Page } from "@playwright/test";
import { test, expect, type Actor } from "./fixtures";
import { documentVersionSchema } from "../src/api/documentTypes";
import { messageResponseSchema, sourceContentSchema, sourceSchema, threadDetailSchema, type SourceView } from "../src/api/types";

const question = 'Покажи тестовое оформление SYNTHETIC_PRESENTATION_86: **обычный вопрос** <img src=x onerror="document.body.dataset.presentationXss=1">';
const protectedMarker = "SYNTHETIC_PRESENTATION_86";
const longCode = `presentation_code_${"0123456789".repeat(24)}`;

async function submit(page: Page, threadId: string) {
  await page.getByLabel("Вопрос по документам", { exact: true }).fill(question);
  const pending = page.waitForResponse(response => response.request().method() === "POST"
    && response.url().endsWith(`/api/chat/threads/${threadId}/messages`));
  await page.getByRole("button", { name: "Отправить", exact: true }).click();
  const response = await pending;
  expect(response.status()).toBe(200);
  expect(response.headers()["cache-control"]).toBe("private, no-store");
  const result = messageResponseSchema.parse(await response.json());
  expect(result.turn.state).toBe("available");
  return result;
}

async function restoreWelcome(page: Page) {
  await page.getByRole("button", { name: "Новый", exact: true }).click();
  await expect(page.locator(".chat-empty")).toBeVisible();
  await expect(page.getByLabel("Диалог", { exact: true })).toHaveValue("");
}

async function expectUnavailable(student: Actor, thread: { readonly threadId: string; readonly title: string }, source: SourceView) {
  const page = student.page;
  await page.reload();
  await expect(page.getByText("Ответ недоступен: права на один из источников изменились.", { exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "Вопрос", exact: true }).getByText(question, { exact: true })).toBeVisible();
  await expect(page.locator(".answer-body, .source-list li, .source-origin")).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Содержимое источника", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /^Источники ответа/ })).toHaveCount(0);
  await expect(page.getByText(thread.title, { exact: true })).toHaveCount(0);
  await expect(page.getByText(protectedMarker, { exact: true })).toHaveCount(0);
  await expect(page.getByText(longCode, { exact: true })).toHaveCount(0);
  const history = await student.context.request.get(`/api/chat/threads/${thread.threadId}`);
  expect(history.status()).toBe(200);
  expect(history.headers()["cache-control"]).toBe("private, no-store");
  const detail = threadDetailSchema.parse(await history.json());
  expect(detail.turns).toHaveLength(1);
  expect(detail.turns[0]).toMatchObject({ state: "unavailable", answer: null, sources: [], user_input: question });
  const denied = await student.context.request.get(source.url);
  const missing = await student.context.request.get(`/api/documents/${randomUUID()}/versions/${randomUUID()}/content?chunk_id=${randomUUID()}`);
  expect(denied.status()).toBe(404);
  expect(missing.status()).toBe(404);
  expect(await denied.text()).toBe(await missing.text());
  expect(denied.headers()["cache-control"]).toBe("private, no-store");
  expect(denied.headers()["cache-control"]).toBe(missing.headers()["cache-control"]);
}

test("committed_safe_answer_renders_semantics_and_current_source_then_disappears_after_revoke", async ({ actors, scenario }, testInfo) => {
  // Given a real uploaded source, explicit grant and owner thread.
  const page = actors.student.page;
  try {
    await page.emulateMedia({ reducedMotion: "reduce" });
    expect(await page.evaluate(() => matchMedia("(prefers-reduced-motion: reduce)").matches)).toBe(true);
    // When a controlled test-model answer is committed through the normal chat POST.
    const answer = await submit(page, scenario.threadId);
    const source = sourceSchema.parse(answer.turn.sources.find(item => item.document_id === scenario.documentId));
    const body = page.locator(".answer-body");
    // Then only the supported semantic nodes appear; hostile content stays readable text.
    await expect(body.locator("ol > li")).toHaveCount(2);
    await expect(body.locator("ul > li")).toHaveCount(2);
    await expect(body.locator("strong")).toHaveText(protectedMarker);
    await expect(body.locator("em")).toHaveText("тестовое выделение");
    await expect(body.locator("p code")).toHaveText("inline_code_86");
    await expect(body.locator("pre > code")).toHaveText(`${longCode}\n`);
    expect(await body.evaluate(element => element.getAnimations({ subtree: true })
      .every(animation => animation.playState !== "running"))).toBe(true);
    for (const payload of [
      '<img src=x onerror="document.body.dataset.presentationXss=1">',
      '<script>document.body.dataset.presentationXss=1</script>',
      '<svg onload="document.body.dataset.presentationXss=1"></svg>',
      '<iframe src="https://example.invalid/frame"></iframe>',
      '[FORGED_SOURCE_86](/api/documents/forged/versions/forged/content?chunk_id=forged)',
      '[unsafe](javascript:alert(1))',
      '![image](https://example.invalid/image.png)',
    ] as const) await expect(body).toContainText(payload);
    await expect(body.locator("img, a, script, svg, iframe, button")).toHaveCount(0);
    expect(await page.evaluate(() => document.body.dataset["presentationXss"])).toBeUndefined();
    const userInput = page.getByRole("region", { name: "Вопрос", exact: true });
    await expect(userInput.getByText(question, { exact: true })).toBeVisible();
    await expect(userInput.locator("strong, img, a")).toHaveCount(0);
    await page.reload();
    await expect(page.locator(".answer-body strong")).toHaveText(protectedMarker);
    const history = await actors.student.context.request.get(`/api/chat/threads/${scenario.threadId}`);
    expect(history.status()).toBe(200);
    const canonical = threadDetailSchema.parse(await history.json());
    expect(canonical.turns).toHaveLength(1);
    expect(canonical.turns[0]).toMatchObject({ state: "available", answer: answer.turn.answer, user_input: question });
    for (const width of [1600, 1280, 768, 375]) {
      await page.setViewportSize({ width, height: 900 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      await page.screenshot({ path: testInfo.outputPath(`answer-committed-${String(width)}.png`), fullPage: true });
    }
    await page.setViewportSize({ width: 375, height: 900 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    const codeBlock = page.locator(".answer-body pre");
    expect(await codeBlock.evaluate(element => element.scrollWidth > element.clientWidth
      && getComputedStyle(element).overflowX === "auto")).toBe(true);
    await codeBlock.evaluate(element => { element.scrollLeft = 80; });
    expect(await codeBlock.evaluate(element => element.scrollLeft)).toBeGreaterThan(0);
    const sourceToggle = page.getByRole("button", { name: /^Источники ответа/ });
    await sourceToggle.focus();
    await page.keyboard.press("Enter");
    const sourceButtons = page.locator(".source-list button");
    await expect(sourceButtons).toHaveCount(answer.turn.sources.length);
    const openSource = page.getByRole("button", { name: `[1] ${scenario.title}`, exact: true });
    await openSource.focus();
    const opened = page.waitForResponse(response => response.url().endsWith(source.url));
    await page.keyboard.press("Enter");
    const sourceResponse = await opened;
    expect(sourceResponse.status()).toBe(200);
    expect(sourceResponse.headers()["cache-control"]).toBe("private, no-store");
    const content = sourceContentSchema.parse(await sourceResponse.json());
    expect(content.policy_revision).toBeGreaterThan(answer.thread_revision);
    expect(content.origin).toBeNull();
    const inspector = page.getByRole("region", { name: "Содержимое источника", exact: true });
    await expect(inspector.getByText(`Проверено по ревизии ${String(content.policy_revision)}`, { exact: true })).toBeVisible();
    await expect(inspector.getByText("Происхождение публикации не указано", { exact: true })).toBeVisible();
    await expect(inspector.getByText(content.text, { exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await inspector.scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath("source-inspector-375-viewport.png"), fullPage: false });
    await page.screenshot({ path: testInfo.outputPath("source-inspector-375.png"), fullPage: true });
    const close = inspector.getByRole("button", { name: "Закрыть", exact: true });
    await close.focus();
    await page.keyboard.press("Enter");
    await expect(inspector).toHaveCount(0);
    await openSource.focus();
    await page.keyboard.press("Enter");
    await expect(inspector).toBeVisible();
    const registry = actors.admin.page;
    await registry.goto("/documents");
    const entry = registry.getByRole("region", { name: "Реестр документов", exact: true })
      .getByRole("listitem").filter({ hasText: scenario.title });
    await expect(entry.getByText("Происхождение публикации не указано", { exact: true })).toBeVisible();
    await entry.getByRole("button").click();
    const versions = registry.getByRole("region", { name: "Версии документа", exact: true });
    await expect(versions.getByText(source.document_version_id, { exact: true })).toBeVisible();
    await expect(versions.getByText("Происхождение публикации не указано", { exact: true })).toBeVisible();
    for (const width of [1600, 375]) {
      await registry.setViewportSize({ width, height: 900 });
      expect(await registry.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      await registry.screenshot({ path: testInfo.outputPath(`documents-registry-versions-${String(width)}.png`), fullPage: true });
    }
    const revoked = await actors.admin.context.request.delete(
      `/api/admin/documents/${scenario.documentId}/grants/${scenario.grantId}`, { headers: actors.admin.headers },
    );
    expect(revoked.status()).toBe(204);
    await expectUnavailable(actors.student, scenario, source);
    await page.screenshot({ path: testInfo.outputPath("answer-unavailable-after-revoke-375.png"), fullPage: true });
  } finally {
    await page.setViewportSize({ width: 1600, height: 900 });
    await page.emulateMedia({ reducedMotion: "no-preference" });
    await actors.admin.page.setViewportSize({ width: 1600, height: 900 });
    await restoreWelcome(page);
  }
});

test("active_version_replacement_hides_old_formatted_answer_and_denies_saved_source", async ({ actors, scenario }) => {
  // Given a committed answer that depends on the original active version.
  const page = actors.student.page;
  const answer = await submit(page, scenario.threadId);
  const source = sourceSchema.parse(answer.turn.sources.find(item => item.document_id === scenario.documentId));
  await expect(page.locator(".answer-body strong")).toHaveText(protectedMarker);
  expect((await actors.student.context.request.get(source.url)).status()).toBe(200);
  // When the normal upload API atomically activates a new immutable version.
  const uploaded = await actors.admin.context.request.post(`/api/admin/documents/${scenario.documentId}/versions`, {
    headers: actors.admin.headers,
    multipart: { file: { name: "replacement.txt", mimeType: "text/plain", buffer: Buffer.from("Новая версия: SYNTHETIC_REPLACEMENT_86.") } },
  });
  expect(uploaded.status()).toBe(201);
  const version = documentVersionSchema.parse(await uploaded.json());
  expect(version.document_id).toBe(scenario.documentId);
  expect(version.id).not.toBe(source.document_version_id);
  expect(version.status).toBe("ready");
  // Then old protected output disappears on a fresh read and its URL is indistinguishable from missing.
  await expectUnavailable(actors.student, scenario, source);
  await restoreWelcome(page);
});
