import { randomUUID } from "node:crypto";
import { test, expect } from "./fixtures";
import { messageResponseSchema, threadDetailSchema } from "../src/api/types";
import { roleSchema } from "../src/api/accessTypes";

test("revoked_answer_disappears_after_refetch_and_saved_source_is_denied", async ({ studentPage, adminPage, actors, scenario }) => {
  await studentPage.getByLabel("Вопрос по документам", { exact: true }).fill("Каков учебный код проекта?");
  const generated = studentPage.waitForResponse(response => response.url().endsWith(`/api/chat/threads/${scenario.threadId}/messages`));
  await studentPage.getByRole("button", { name: "Отправить", exact: true }).click();
  const response = await generated;
  expect(response.status()).toBe(200);
  const answer = messageResponseSchema.parse(await response.json());
  expect(answer.turn.state).toBe("available");
  const source = answer.turn.sources.find(value => value.document_id === scenario.documentId);
  if (!source) throw new Error("Expected managed source provenance");
  await expect(studentPage.getByText("SYNTHETIC_REVOKE_57", { exact: true })).toBeVisible();
  await studentPage.getByRole("button", { name: /^Источники ответа/ }).click();
  await studentPage.getByRole("button", { name: `[1] ${scenario.title}`, exact: true }).click();
  expect((await actors.student.context.request.get(source.url)).status()).toBe(200);
  await adminPage.goto(`/access?document=${scenario.documentId}`);
  await adminPage.getByRole("button", { name: /^Отозвать прямое разрешение:/ }).click();
  await adminPage.getByRole("button", { name: "Подтвердить отзыв", exact: true }).click();
  await expect(adminPage.getByRole("button", { name: /^Отозвать прямое разрешение:/ })).toHaveCount(0);
  await studentPage.reload();
  await expect(studentPage.getByText("Ответ недоступен: права на один из источников изменились.", { exact: true })).toBeVisible();
  await expect(studentPage.getByText("SYNTHETIC_REVOKE_57", { exact: true })).toHaveCount(0);
  await expect(studentPage.getByText(scenario.title, { exact: true })).toHaveCount(0);
  const detailResponse = await actors.student.context.request.get(`/api/chat/threads/${scenario.threadId}`);
  expect(detailResponse.headers()["cache-control"]).toBe("private, no-store");
  const detail = threadDetailSchema.parse(await detailResponse.json());
  expect(detail.turns[0]).toMatchObject({ state: "unavailable", answer: null, sources: [], user_input: "Каков учебный код проекта?" });
  const denied = await actors.student.context.request.get(source.url);
  const missing = await actors.student.context.request.get(`/api/documents/${randomUUID()}/versions/${randomUUID()}/content?chunk_id=${randomUUID()}`);
  expect(denied.status()).toBe(404); expect(missing.status()).toBe(404);
  expect(await denied.text()).toBe(await missing.text());
  expect(denied.headers()["cache-control"]).toBe("private, no-store");
  expect(denied.headers()["cache-control"]).toBe(missing.headers()["cache-control"]);
  await studentPage.getByLabel("Вопрос по документам", { exact: true }).fill("Повтори прежний ответ");
  await studentPage.getByRole("button", { name: "Отправить", exact: true }).click();
  await expect(studentPage.getByText("Нет доступных источников для ответа.", { exact: true })).toBeVisible();
  await expect(studentPage.getByText("SYNTHETIC_REVOKE_57", { exact: true })).toHaveCount(0);
});

test("direct_grant_survives_role_removal", async ({ studentPage, adminPage, actors, scenario }) => {
  const roleResponse = await actors.admin.context.request.post("/api/admin/roles", {
    headers: actors.admin.headers, data: { code: `e2e_${randomUUID().replaceAll("-", "")}`, display_name: "Synthetic role" },
  });
  expect(roleResponse.status()).toBe(201);
  const role = roleSchema.parse(await roleResponse.json());
  const membership = `/api/admin/roles/${role.id}/members/${actors.student.id}`;
  expect((await actors.admin.context.request.put(membership, { headers: actors.admin.headers })).status()).toBe(204);
  expect((await actors.admin.context.request.post(`/api/admin/documents/${scenario.documentId}/grants`, {
    headers: actors.admin.headers, data: { role_id: role.id },
  })).status()).toBe(201);
  await studentPage.getByLabel("Вопрос по документам", { exact: true }).fill("Каков учебный код проекта?");
  const generated = studentPage.waitForResponse(response => response.url().endsWith(`/api/chat/threads/${scenario.threadId}/messages`));
  await studentPage.getByRole("button", { name: "Отправить", exact: true }).click();
  const answer = messageResponseSchema.parse(await (await generated).json());
  const source = answer.turn.sources.find(value => value.document_id === scenario.documentId);
  if (!source) throw new Error("Expected managed source provenance");
  await adminPage.goto(`/access?document=${scenario.documentId}`);
  await adminPage.getByText("Роли и участники", { exact: true }).click();
  await adminPage.getByLabel("Роль для управления", { exact: true }).selectOption(role.id);
  await adminPage.getByRole("button", { name: "Удалить из роли", exact: true }).click();
  await adminPage.getByRole("button", { name: "Подтвердить удаление", exact: true }).click();
  await expect(adminPage.getByText("У роли нет участников.", { exact: true })).toBeVisible();
  await studentPage.reload();
  await expect(studentPage.getByText("SYNTHETIC_REVOKE_57", { exact: true })).toBeVisible();
  expect((await actors.student.context.request.get(source.url)).status()).toBe(200);
  await adminPage.getByRole("button", { name: /^Отозвать прямое разрешение:/ }).click();
  await adminPage.getByRole("button", { name: "Подтвердить отзыв", exact: true }).click();
  await expect(adminPage.getByRole("button", { name: /^Отозвать прямое разрешение:/ })).toHaveCount(0);
  await studentPage.reload();
  await expect(studentPage.getByText("SYNTHETIC_REVOKE_57", { exact: true })).toHaveCount(0);
  await expect(studentPage.getByText("Ответ недоступен: права на один из источников изменились.", { exact: true })).toBeVisible();
  expect((await actors.student.context.request.get(source.url)).status()).toBe(404);
});
