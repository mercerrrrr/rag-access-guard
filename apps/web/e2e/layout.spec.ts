import { test, expect } from "./fixtures";

for (const width of [1600, 375]) {
  test(`authenticated_layout_${String(width)}_and_keyboard`, async ({ actors }) => {
    const page = actors.teacher.page;
    await page.setViewportSize({ width, height: 900 });
    await page.emulateMedia({ reducedMotion: "reduce" });
    await expect(page.getByRole("button", { name: "Новый", exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    expect(await page.evaluate(() => matchMedia("(prefers-reduced-motion: reduce)").matches)).toBe(true);
    await page.getByRole("button", { name: "Новый", exact: true }).focus();
    await page.keyboard.press("Enter");
    const question = page.getByLabel("Вопрос по документам", { exact: true });
    await expect(question).toBeVisible();
    await question.focus();
    await expect(question).toBeFocused();
    await question.fill("Проверка клавиатуры");
    await page.keyboard.press("Tab");
    await expect(page.getByRole("button", { name: "Отправить", exact: true })).toBeFocused();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.setViewportSize({ width: 1600, height: 900 });
  });
}
