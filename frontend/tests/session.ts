import { expect, type Page } from "@playwright/test";

export async function login(page: Page, email: string) {
  const password = process.env.SCEPKA_DEMO_PASSWORD;
  if (!password)
    throw new Error(
      "Задайте SCEPKA_DEMO_PASSWORD для отдельного демонстрационного окружения",
    );
  await page.goto("/login");
  await page.getByLabel("Электронная почта").fill(email);
  await page.getByLabel("Пароль", { exact: true }).fill(password);
  const receipt = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/auth/login") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Войти", exact: true }).click();
  const result = await (await receipt).json();
  await expect(page).toHaveURL(/\/(candidate|employer)$/);
  await expect(
    page.getByRole("navigation", { name: "Навигация кабинета" }),
  ).toBeVisible();
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  return result;
}
