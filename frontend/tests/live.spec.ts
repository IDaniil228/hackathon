import { test, expect } from "@playwright/test";
import { capture } from "./capture";
import { login } from "./session";

test("настоящий подбор, карты и предложение с перепиской", async ({
  page,
  browser,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await login(page, "employer@scepka.example");
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Сильная команда начинается здесь" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Светлая", exact: true }).click();
  await capture(page, "employer-dashboard-light");
  await page.goto("/employer/company");
  await expect(page.getByLabel("Имя представителя")).toHaveValue(
    "Александра Миронова",
  );
  await page.goto("/employer/needs");
  await page
    .getByRole("link", { name: "Backend для платёжной платформы" })
    .click();
  await page.getByRole("button", { name: "Начать подбор" }).click();
  await expect(page).toHaveURL(/\/employer\/runs\//, { timeout: 60000 });
  await expect(page.getByText("Готово", { exact: true })).toBeVisible({
    timeout: 150000,
  });
  await expect(page.locator(".map-dot").first()).toBeVisible({
    timeout: 60000,
  });
  expect(await page.locator(".map-dot").count()).toBeGreaterThan(30);
  await capture(page, "search-cloud-light", false);
  await page.getByRole("button", { name: "Радиальная", exact: true }).click();
  await page.getByRole("button", { name: "Тёмная", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Радиальная", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await capture(page, "search-radial-dark", false);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await capture(page, "search-mobile-dark", false);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/employer/candidates");
  await expect(page.locator(".candidate-card").first()).toBeVisible();
  await page.getByRole("button", { name: "Светлая", exact: true }).click();
  await capture(page, "candidates-light", false);
  const first = page.locator(".candidate-card").first();
  await first.locator("h3 a").click();
  await page.getByRole("button", { name: "Направить предложение" }).click();
  await page
    .getByLabel("Предложение и условия работы")
    .fill(
      "Приглашаем обсудить разработку платёжных сервисов. Работа удалённо, команда с код-ревью",
    );
  await page.getByLabel("Зарплата от, ₽").fill("240000");
  await page.getByLabel("Зарплата до, ₽").fill("320000");
  await page
    .getByRole("button", { name: "Отправить предложение", exact: true })
    .click();
  await expect(page).toHaveURL(/\/employer\/conversations\//);
  await page
    .getByLabel("Сообщение", { exact: true })
    .fill("Здравствуйте! Когда вам удобно познакомиться с командой?");
  await page.getByRole("button", { name: "Отправить", exact: true }).click();
  await expect(
    page.getByText("Здравствуйте! Когда вам удобно познакомиться с командой?", {
      exact: true,
    }),
  ).toBeVisible();
  await capture(page, "conversation-light");
  const candidate = await browser.newPage({
    viewport: { width: 390, height: 844 },
  });
  await login(candidate, "candidate@scepka.example");
  await candidate.goto("/candidate/profile");
  await expect(candidate.getByLabel("Имя", { exact: true })).not.toHaveValue(
    "",
  );
  expect(
    await candidate.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await capture(candidate, "profile-mobile-light");
  await candidate.goto("/candidate/assessment");
  await expect(
    candidate.getByRole("heading", { name: "Входной опрос" }),
  ).toBeVisible();
  await capture(candidate, "assessment-mobile");
  await candidate.close();
  expect(errors).toEqual([]);
});
