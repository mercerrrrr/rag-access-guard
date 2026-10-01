import { randomUUID } from "node:crypto";
import { test as base, expect, type Browser, type BrowserContext, type Page } from "@playwright/test";
import { z } from "zod";
import { loginSchema, threadSchema } from "../src/api/types";
import { adminDocumentSchema } from "../src/api/documentTypes";
import { grantSchema } from "../src/api/accessTypes";

export const origin = "http://127.0.0.1:54174";
export type Actor = { context: BrowserContext; page: Page; id: string; headers: Record<string, string> };
export type E2eActors = { student: Actor; teacher: Actor; staff: Actor; admin: Actor };
type Scenario = { documentId: string; threadId: string; grantId: string; title: string };

async function actor(browser: Browser, login: string): Promise<Actor> {
  const context = await browser.newContext({ baseURL: origin, viewport: { width: 1600, height: 900 }, locale: "ru-RU" });
  const page = await context.newPage();
  await page.goto("/login");
  await page.getByLabel("Логин", { exact: true }).fill(login);
  await page.getByLabel("Пароль", { exact: true }).fill(z.string().min(1).parse(process.env["RAG_E2E_PASSWORD"]));
  const response = page.waitForResponse(value => value.url().endsWith("/api/auth/login"));
  await page.getByRole("button", { name: "Войти", exact: true }).click();
  const loggedIn = await response;
  expect(loggedIn.status()).toBe(200);
  const session = loginSchema.parse(await loggedIn.json());
  await expect(page).toHaveURL(`${origin}/chat`);
  return { context, page, id: session.user.id, headers: { Origin: origin, "X-CSRF-Token": session.csrf_token } };
}

export const test = base.extend<{ scenario: Scenario; studentPage: Page; adminPage: Page }, { actors: E2eActors }>({
  actors: [async ({ browser }, use) => {
    const student = await actor(browser, "student"), teacher = await actor(browser, "teacher");
    const staff = await actor(browser, "staff"), admin = await actor(browser, "admin");
    try { await use({ student, teacher, staff, admin }); }
    finally { await Promise.all([student, teacher, staff, admin].map(value => value.context.close())); }
  }, { scope: "worker" }],
  studentPage: async ({ actors }, use) => { await use(actors.student.page); },
  adminPage: async ({ actors }, use) => { await use(actors.admin.page); },
  scenario: async ({ actors }, use) => {
    const title = `Synthetic document ${randomUUID()}`;
    const response = await actors.admin.context.request.post("/api/admin/documents", {
      headers: actors.admin.headers,
      multipart: { title, file: { name: "synthetic.txt", mimeType: "text/plain", buffer: Buffer.from("Учебный код проекта: SYNTHETIC_REVOKE_57.") } },
    });
    expect(response.status()).toBe(201);
    const document = adminDocumentSchema.parse(await response.json());
    const given = await actors.admin.context.request.post(`/api/admin/documents/${document.id}/grants`, {
      headers: actors.admin.headers, data: { user_id: actors.student.id },
    });
    expect(given.status()).toBe(201);
    const grant = grantSchema.parse(await given.json());
    const created = await actors.student.context.request.post("/api/chat/threads", { headers: actors.student.headers, data: {} });
    expect(created.status()).toBe(201);
    const thread = threadSchema.parse(await created.json());
    await actors.student.page.goto(`/chat/${thread.id}`);
    try { await use({ documentId: document.id, threadId: thread.id, grantId: grant.id, title }); }
    finally {
      const removed = await actors.admin.context.request.delete(`/api/admin/documents/${document.id}/grants/${grant.id}`, { headers: actors.admin.headers });
      expect([204, 404]).toContain(removed.status());
    }
  },
});
export { expect };
