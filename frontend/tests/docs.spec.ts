import { test, expect } from "@playwright/test";
const base = process.env.DOCS_BASE_PATH || "/hackathon/";

test("документация работает без API под префиксом Pages", async ({ page }) => {
  const apiRequests: string[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.startsWith("/api/"))
      apiRequests.push(request.url());
  });
  await page.goto(base);
  await expect(page.locator(".docs-layout")).toBeVisible();
  await page
    .getByRole("button", { name: "frontend / Архитектура", exact: true })
    .click();
  await expect(page.locator(".diagram svg").first()).toBeVisible();
  await page
    .getByRole("button", {
      name: "frontend / Проверки и результаты",
      exact: true,
    })
    .click();
  await expect(
    page.getByRole("heading", { name: "Проверка Сцепки", exact: true }),
  ).toBeVisible();
  const screenshot = page
    .getByRole("link", { name: "Открыть", exact: true })
    .first();
  const asset = await screenshot.getAttribute("href");
  expect(asset?.startsWith(`${base}assets/`)).toBe(true);
  expect(asset?.endsWith(".png")).toBe(true);
  expect((await page.request.get(asset!)).status()).toBe(200);
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Проверка Сцепки", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("link", { name: "инструкции запуска", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Локальный запуск Сцепки", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Тёмная", exact: true }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  expect(apiRequests).toEqual([]);
});
