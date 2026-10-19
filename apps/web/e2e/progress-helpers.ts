import { randomUUID } from "node:crypto";
import type { Page, Request } from "@playwright/test";
import { z } from "zod";
import { expect, origin, type E2eActors } from "./fixtures";
import { loginSchema, requestStatusSchema, threadDetailSchema } from "../src/api/types";

const submissionSchema = z.strictObject({
  request_id: z.uuid(), expected_thread_revision: z.number().int().nonnegative(), user_input: z.string(),
});
export type Submission = z.infer<typeof submissionSchema>;
export const answerMarker = "SYNTHETIC_REVOKE_57";
export const generation = (page: Page) => page.getByRole("status").filter({ hasText: "Готовим ответ" });

export async function control(actors: E2eActors, suffix: string, data?: unknown) {
  const response = await actors.admin.context.request.post(`/__e2e/progress/holds${suffix}`, {
    headers: { "X-E2E-Control": z.string().min(1).parse(process.env["RAG_E2E_PASSWORD"]) },
    ...(data === undefined ? {} : { data }),
  });
  expect(response.status()).toBe(204);
}

export async function withHeldQuestion(
  actors: E2eActors, threadId: string, question: string,
  run: (submission: Submission, release: () => Promise<void>) => Promise<void>,
) {
  const holdId = randomUUID();
  await control(actors, "", { hold_id: holdId, question });
  const release = () => control(actors, `/${holdId}/release`);
  try {
    const page = actors.student.page;
    const submitted = page.waitForRequest(request => request.method() === "POST"
      && request.url().endsWith(`/api/chat/threads/${threadId}/messages`));
    await page.getByLabel("Вопрос по документам", { exact: true }).fill(question);
    await page.getByLabel("Вопрос по документам", { exact: true }).press("Enter");
    const submission = parseSubmission(await submitted);
    await expect(page.locator(".chat-transcript").getByText(question, { exact: true })).toBeVisible();
    await control(actors, `/${holdId}/entered`);
    await expect(generation(page)).toBeVisible();
    await run(submission, release);
  } finally {
    await release();
  }
}

export function parseSubmission(request: Request): Submission {
  return submissionSchema.parse(request.postDataJSON());
}

export async function browserStatus(page: Page, threadId: string, requestId: string) {
  const result = await page.evaluate(async path => {
    const response = await fetch(path, { credentials: "same-origin", cache: "no-store" });
    const body: unknown = await response.json();
    return { status: response.status, cache: response.headers.get("cache-control"), body };
  }, `/api/chat/threads/${threadId}/requests/${requestId}/status`);
  expect(result.status).toBe(200);
  expect(result.cache).toBe("private, no-store");
  expect(Object.keys(z.record(z.string(), z.unknown()).parse(result.body)).sort())
    .toEqual(["elapsed_seconds", "request_id", "stage", "state"]);
  return requestStatusSchema.parse(result.body);
}

export async function waitForTerminal(actors: E2eActors, threadId: string, requestId: string) {
  await expect.poll(async () => {
    const response = await actors.student.context.request.get(
      `/api/chat/threads/${threadId}/requests/${requestId}/status`, { headers: { Origin: origin } },
    );
    expect(response.status()).toBe(200);
    return requestStatusSchema.parse(await response.json()).state;
  }).toBe("terminal");
}

export async function canonicalThread(actors: E2eActors, threadId: string) {
  const response = await actors.student.context.request.get(`/api/chat/threads/${threadId}`);
  expect(response.status()).toBe(200);
  return threadDetailSchema.parse(await response.json());
}

export async function restoreStudentLogin(actors: E2eActors) {
  const page = actors.student.page;
  if (new URL(page.url()).pathname !== "/login") await page.goto("/login");
  const destination = new URL(page.url()).searchParams.get("returnTo") ?? "/chat";
  expect(destination).toMatch(/^\/chat(?:\/[0-9a-f-]{36})?$/u);
  await page.getByLabel("Логин", { exact: true }).fill("student");
  await page.getByLabel("Пароль", { exact: true }).fill(z.string().min(1).parse(process.env["RAG_E2E_PASSWORD"]));
  const response = page.waitForResponse(value => value.url().endsWith("/api/auth/login"));
  await page.getByRole("button", { name: "Войти", exact: true }).click();
  const loggedIn = await response;
  expect(loggedIn.status()).toBe(200);
  const session = loginSchema.parse(await loggedIn.json());
  expect(session.user.id).toBe(actors.student.id);
  expect(session.user.login).toBe("student");
  actors.student.headers = { Origin: origin, "X-CSRF-Token": session.csrf_token };
  await expect(page).toHaveURL(`${origin}${destination}`);
}
