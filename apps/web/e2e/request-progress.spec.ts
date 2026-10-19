import { randomUUID } from "node:crypto";
import { test, expect } from "./fixtures";
import { messageResponseSchema, sourceSchema, threadSchema } from "../src/api/types";
import {
  answerMarker, browserStatus, canonicalThread, generation, parseSubmission,
  restoreStudentLogin, waitForTerminal, withHeldQuestion,
} from "./progress-helpers";

test("held_generation_shows_escaped_question_closed_status_and_responsive_accessible_progress", async ({ actors, scenario }, testInfo) => {
  const page = actors.student.page;
  const question = `Каков учебный код проекта? <img src=x onerror="document.body.dataset.xss='executed'"> ${randomUUID()}`;
  await page.emulateMedia({ reducedMotion: "reduce" });
  await withHeldQuestion(actors, scenario.threadId, question, async (submission, release) => {
    await expect(page.getByText(answerMarker, { exact: true })).toHaveCount(0);
    await expect(page.getByText(scenario.title, { exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: /^Источники ответа/ })).toHaveCount(0);
    expect(await page.evaluate(() => document.body.dataset["xss"])).toBeUndefined();
    await expect(page.locator(".chat-transcript img")).toHaveCount(0);
    const observed = page.waitForRequest(request => request.url().endsWith(`/requests/${submission.request_id}/status`));
    const status = await browserStatus(page, scenario.threadId, submission.request_id);
    expect(status).toMatchObject({ state: "running", stage: "generation", request_id: submission.request_id });
    const request = await observed;
    const headers = await request.allHeaders();
    expect(headers["origin"]).toBeUndefined();
    expect(headers["sec-fetch-site"]).toBe("same-origin");
    expect(JSON.stringify(status)).not.toContain(answerMarker);
    const pending = await canonicalThread(actors, scenario.threadId);
    expect(pending.turns).toHaveLength(1);
    expect(pending.turns[0]).toMatchObject({ state: "pending", answer: null, sources: [] });
    await expect(generation(page)).toHaveAttribute("aria-live", "polite");
    expect(await page.locator(".request-progress").evaluate(element => element.getAnimations({ subtree: true })
      .every(animation => animation.playState !== "running"))).toBe(true);
    for (const [width, height] of [[375, 900], [768, 900], [1280, 900], [1600, 900], [800, 450]] as const) {
      await page.setViewportSize({ width, height });
      await expect(generation(page)).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      const path = testInfo.outputPath(`progress-${String(width)}x${String(height)}.png`);
      await page.screenshot({ path, fullPage: true });
      await testInfo.attach(`progress-${String(width)}x${String(height)}${width === 800 ? "-effective-viewport" : ""}`, { path, contentType: "image/png" });
    }
    await page.getByRole("button", { name: "Остановить ожидание", exact: true }).focus();
    await expect(page.getByRole("button", { name: "Остановить ожидание", exact: true })).toBeFocused();
    const completed = page.waitForResponse(response => response.url().endsWith(`/threads/${scenario.threadId}/messages`));
    await release();
    const response = messageResponseSchema.parse(await (await completed).json());
    expect(response.turn.state).toBe("available");
    const source = sourceSchema.parse(response.turn.sources.find(item => item.document_id === scenario.documentId));
    expect((await actors.student.context.request.get(source.url)).status()).toBe(200);
    await expect(page.getByText(answerMarker, { exact: true })).toBeVisible();
    await expect(page.locator(".chat-transcript").getByText(question, { exact: true })).toHaveCount(1);
    await expect(generation(page)).toHaveCount(0);
    expect((await canonicalThread(actors, scenario.threadId)).turns).toHaveLength(1);
    expect((await browserStatus(page, scenario.threadId, submission.request_id)).state).toBe("terminal");
    await page.setViewportSize({ width: 1600, height: 900 });
    await page.emulateMedia({ reducedMotion: "no-preference" });
  });
});

test("switching_thread_discards_late_progress_and_answer", async ({ actors, scenario }) => {
  const page = actors.student.page;
  const created = await actors.student.context.request.post("/api/chat/threads", { headers: actors.student.headers, data: {} });
  expect(created.status()).toBe(201);
  const other = threadSchema.parse(await created.json());
  await page.getByRole("button", { name: "Обновить", exact: true }).click();
  const question = `Каков учебный код проекта? ${randomUUID()}`;
  await withHeldQuestion(actors, scenario.threadId, question, async (submission, release) => {
    await page.getByLabel("Диалог", { exact: true }).selectOption(other.id);
    await expect(page).toHaveURL(new RegExp(`/chat/${other.id}$`));
    await release();
    await waitForTerminal(actors, scenario.threadId, submission.request_id);
    await expect(page.locator(".chat-transcript").getByText(question, { exact: true })).toHaveCount(0);
    await expect(page.getByText(answerMarker, { exact: true })).toHaveCount(0);
    await expect(generation(page)).toHaveCount(0);
    expect((await canonicalThread(actors, other.id)).turns).toHaveLength(0);
  });
});

test("logout_during_generation_discards_late_protected_dom", async ({ actors, scenario }) => {
  const page = actors.student.page;
  const question = `Каков учебный код проекта? ${randomUUID()}`;
  try {
    await withHeldQuestion(actors, scenario.threadId, question, async (_submission, release) => {
      await page.getByRole("button", { name: "Выйти", exact: true }).click();
      await expect(page).toHaveURL(url => url.pathname === "/login");
      await release();
      await page.goBack();
      await expect(page).toHaveURL(url => url.pathname === "/login");
      await expect(page.getByText(question, { exact: true })).toHaveCount(0);
      await expect(page.getByText(answerMarker, { exact: true })).toHaveCount(0);
      await expect(generation(page)).toHaveCount(0);
      expect((await actors.student.context.request.get(`/api/chat/threads/${scenario.threadId}`)).status()).toBe(401);
    });
  } finally {
    await restoreStudentLogin(actors);
  }
});

for (const recovery of ["retry", "history"] as const) {
  test(`cancel_then_${recovery}_recovers_one_canonical_question`, async ({ actors, scenario }) => {
    const page = actors.student.page;
    const question = `Каков учебный код проекта? ${randomUUID()}`;
    await withHeldQuestion(actors, scenario.threadId, question, async (submission, release) => {
      await page.getByRole("button", { name: "Остановить ожидание", exact: true }).press("Enter");
      await expect(page.locator(".chat-feedback").getByText(/Ожидание остановлено\. Сервер мог продолжить обработку/)).toBeVisible();
      await expect(generation(page)).toHaveCount(0);
      await release();
      await waitForTerminal(actors, scenario.threadId, submission.request_id);
      if (recovery === "retry") {
        const retried = page.waitForRequest(request => request.method() === "POST"
          && request.url().endsWith(`/threads/${scenario.threadId}/messages`));
        const completed = page.waitForResponse(response => response.url().endsWith(`/threads/${scenario.threadId}/messages`));
        await page.getByRole("button", { name: "Повторить тот же запрос", exact: true }).click();
        expect(parseSubmission(await retried)).toEqual(submission);
        expect(messageResponseSchema.parse(await (await completed).json()).replayed).toBe(true);
      } else {
        await page.getByRole("button", { name: "Проверить историю и продолжить", exact: true }).click();
      }
      await expect(page.getByText(answerMarker, { exact: true })).toBeVisible();
      await expect(page.locator(".chat-transcript").getByText(question, { exact: true })).toHaveCount(1);
      expect((await canonicalThread(actors, scenario.threadId)).turns).toHaveLength(1);
    });
  });
}

test("revoking_grant_before_release_prevents_held_answer", async ({ actors, scenario }) => {
  const page = actors.student.page;
  await withHeldQuestion(actors, scenario.threadId, `Каков учебный код проекта? ${randomUUID()}`, async (submission, release) => {
    const revoked = await actors.admin.context.request.delete(
      `/api/admin/documents/${scenario.documentId}/grants/${scenario.grantId}`, { headers: actors.admin.headers },
    );
    expect(revoked.status()).toBe(204);
    await release();
    await waitForTerminal(actors, scenario.threadId, submission.request_id);
    await expect(page.getByText(answerMarker, { exact: true })).toHaveCount(0);
    await expect(generation(page)).toHaveCount(0);
    const detail = await canonicalThread(actors, scenario.threadId);
    expect(detail.turns).toHaveLength(1);
    expect(detail.turns[0]).toMatchObject({ state: "neutral", answer: null, sources: [] });
  });
});
