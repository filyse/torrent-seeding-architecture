import { test, expect } from "@playwright/test";

test.describe("smoke", () => {
  test("главная страница с заголовком", async ({ page }) => {
    test.skip(
      !process.env.PLAYWRIGHT_BASE_URL,
      "Задайте PLAYWRIGHT_BASE_URL (например http://127.0.0.1:5173) и выполните npx playwright install",
    );
    await page.goto("/");
    await expect(page.locator("h1")).toBeVisible();
  });
});
