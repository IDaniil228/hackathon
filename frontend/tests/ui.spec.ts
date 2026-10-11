import { test, expect } from "@playwright/test";
import { capture } from "./capture";
import type { components } from "../src/api/schema";

test("редактор появляется только после выдачи проверенного набора", async ({
  page,
}) => {
  let issued = false;
  const model: components["schemas"]["TestOut"] = {
    id: "00000000-0000-4000-8000-000000000001",
    candidate_id: "00000000-0000-4000-8000-000000000002",
    specialization_id: 1,
    claimed_grade_id: 1,
    status: "in_progress",
    created_at: "2026-10-11T00:00:00Z",
    deadline_at: "2099-10-11T00:00:00Z",
    service_state: "ready",
    tasks_issued: true,
    result_applied: false,
    tasks: [
      {
        id: "00000000-0000-4000-8000-000000000003",
        status: "ready",
        kind: "code",
        position: 1,
        statement:
          "Напишите функцию `total(values)`, которая возвращает сумму чисел списка",
        public: {
          title: "Сумма платежей",
          signature: { runtime: "python", io: "function", entrypoint: "total" },
          examples: [{ input: [[1, 2]], expected: 3 }],
        },
      },
    ],
  };
  await page.route(
    (url) => url.pathname.startsWith("/api/"),
    async (route) => {
      const path = new URL(route.request().url()).pathname.replace("/api", "");
      if (path === "/auth/refresh")
        return route.fulfill({
          json: { access_token: "test-only", token_type: "bearer" },
        });
      if (path === "/me")
        return route.fulfill({
          json: {
            id: model.candidate_id,
            roles: ["candidate"],
            email: "ui@example.com",
            email_verified: true,
          },
        });
      if (path === `/tests/${model.id}`)
        return route.fulfill({
          json: issued
            ? model
            : {
                ...model,
                service_state: "generating",
                tasks_issued: false,
                tasks: model.tasks.map((t) => ({
                  ...t,
                  public: null,
                  statement: null,
                  status: "pending",
                })),
              },
        });
      return route.fulfill({
        status: 404,
        json: { detail: "Этот запрос не предусмотрен UI-проверкой" },
      });
    },
  );
  await page.goto(`/candidate/tests/${model.id}`);
  await expect(
    page.getByText("Формируем задания", { exact: true }),
  ).toBeVisible();
  await expect(page.locator(".cm-editor")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Завершить и проверить" }),
  ).toBeDisabled();
  issued = true;
  await expect(
    page.getByRole("heading", { name: "Сумма платежей" }),
  ).toBeVisible();
  await expect(page.locator(".cm-editor")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Завершить и проверить" }),
  ).toBeEnabled();
  await expect(page.getByText("Вывод: 3", { exact: false })).toBeVisible();
});

test("главная, темы и мобильная компоновка", async ({ page }) => {
  await page.route(
    (url) => url.pathname.startsWith("/api/"),
    (route) =>
      route.fulfill({
        status: 403,
        json: { detail: "Для кабинета требуется вход" },
      }),
  );
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Не просто резюме Точка сцепления" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Светлая", exact: true }).click();
  const light = await page.evaluate(
    () => getComputedStyle(document.body).backgroundColor,
  );
  await capture(page, "landing-light");
  await page.getByRole("button", { name: "Тёмная", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Тёмная", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  expect(
    await page.evaluate(() => getComputedStyle(document.body).backgroundColor),
  ).not.toBe(light);
  await capture(page, "landing-dark");
  await page.reload();
  await expect(
    page.getByRole("button", { name: "Тёмная", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "Системная", exact: true }).click();
  await page.emulateMedia({ colorScheme: "light", reducedMotion: "reduce" });
  await expect(page.locator("html")).not.toHaveAttribute("data-theme");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    page.getByRole("link", { name: "Я ищу возможности" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await capture(page, "landing-mobile");
  expect(errors).toEqual([]);
});

test("неверный пароль остаётся понятной ошибкой формы", async ({ page }) => {
  await page.route(
    (url) => url.pathname.startsWith("/api/"),
    (route) =>
      route.fulfill({
        status: 401,
        json: { detail: "Неверная почта или пароль" },
      }),
  );
  await page.goto("/login");
  await page.getByLabel("Электронная почта").fill("nobody@scepka.example");
  await page.getByLabel("Пароль", { exact: true }).fill("not-the-password");
  await page.getByRole("button", { name: "Войти", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Невер");
  await expect(page).toHaveURL(/\/login$/);
});
