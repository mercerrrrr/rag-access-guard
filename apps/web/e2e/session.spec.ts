import { test, expect, origin } from "./fixtures";

test("non_admin_cannot_open_admin_routes", async ({ actors }) => {
  for (const actor of [actors.student, actors.teacher, actors.staff]) {
    for (const path of ["/access", "/audit"]) {
      await actor.page.goto(path);
      await expect(actor.page).toHaveURL(`${origin}/chat`);
    }
    for (const path of ["/api/admin/users", "/api/admin/roles", "/api/admin/audit"]) {
      expect((await actor.context.request.get(path)).status()).toBe(403);
    }
    await expect(actor.page.getByRole("link", { name: "Доступ", exact: true })).toHaveCount(0);
    await expect(actor.page.getByRole("link", { name: "Аудит", exact: true })).toHaveCount(0);
  }
});

test("logout_back_does_not_restore_protected_dom", async ({ actors, scenario }) => {
  const page = actors.student.page;
  await page.getByLabel("Вопрос по документам", { exact: true }).fill("Каков учебный код проекта?");
  await page.getByRole("button", { name: "Отправить", exact: true }).click();
  await expect(page.getByText("SYNTHETIC_REVOKE_57", { exact: true })).toBeVisible();
  await expect(page.getByText("Synthetic student", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Выйти", exact: true }).click();
  await expect(page).toHaveURL(/\/login/);
  await page.goBack();
  await expect(page).toHaveURL(/\/login/);
  await expect(page.getByText("Synthetic student", { exact: true })).toHaveCount(0);
  await expect(page.getByText("SYNTHETIC_REVOKE_57", { exact: true })).toHaveCount(0);
  await expect(page.getByText(scenario.title, { exact: true })).toHaveCount(0);
  expect((await actors.student.context.request.get(`/api/chat/threads/${scenario.threadId}`)).status()).toBe(401);
});
