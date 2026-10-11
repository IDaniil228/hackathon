import { test, expect } from "@playwright/test";
import { capture } from "./capture";
import type { components } from "../src/api/schema";
const userId = "00000000-0000-4000-8000-000000000020";

test("регистрация сразу отправляет письмо подтверждения", async ({ page }) => {
  let authenticated = false;
  let deliveries = 0;
  await page.route(
    (url) => url.pathname.startsWith("/api/"),
    async (route) => {
      const path = new URL(route.request().url()).pathname.replace(
        /^\/api/,
        "",
      );
      if (path === "/auth/refresh")
        return route.fulfill({
          status: 401,
          json: { detail: "Требуется вход" },
        });
      if (path === "/auth/register")
        return route.fulfill({
          status: 201,
          json: { status: "pending", email_verified: false },
        });
      if (path === "/auth/login") {
        authenticated = true;
        return route.fulfill({
          json: { access_token: "test-only", token_type: "bearer" },
        });
      }
      if (path === "/me" && authenticated)
        return route.fulfill({
          json: {
            id: userId,
            email: "registration@example.com",
            roles: ["candidate"],
            email_verified: false,
            demo_auth: false,
          },
        });
      if (path === "/auth/email/request") {
        deliveries++;
        return route.fulfill({ status: 202, json: { status: "accepted" } });
      }
      return route.fulfill({
        status: 404,
        json: { detail: "Запрос не предусмотрен проверкой" },
      });
    },
  );
  await page.goto("/register");
  await page.getByLabel("Имя", { exact: false }).first().fill("Проверка");
  await page.getByLabel("Фамилия", { exact: false }).fill("Регистрации");
  await page
    .getByLabel("Электронная почта", { exact: false })
    .fill("registration@example.com");
  await page
    .getByLabel("Пароль — от 12 символов", { exact: false })
    .fill("Registration-test-2026");
  await page
    .getByLabel("Согласен на обработку персональных данных", { exact: false })
    .check();
  await page
    .getByRole("button", { name: "Создать аккаунт", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Подтвердите почту", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Письмо отправлено. Проверьте входящие", { exact: true }),
  ).toBeVisible();
  expect(deliveries).toBe(1);
});

for (const role of ["candidate", "employer"]) {
  test(`карточки обзора ведут в разделы: ${role}`, async ({ page }) => {
    await page.route(
      (url) => url.pathname.startsWith("/api/"),
      async (route) => {
        const path = new URL(route.request().url()).pathname.replace(
          /^\/api/,
          "",
        );
        if (path === "/auth/refresh")
          return route.fulfill({
            json: { access_token: "test-only", token_type: "bearer" },
          });
        if (path === "/me")
          return route.fulfill({
            json: {
              id: userId,
              email: "dashboard@example.com",
              roles: [role],
              email_verified: true,
            },
          });
        if (path === "/me/profile")
          return route.fulfill({
            json: { first_name: "Кандидат", category: { name: "Backend" } },
          });
        if (path === "/conversations")
          return route.fulfill({ json: { results: [] } });
        return route.fulfill({ json: [] });
      },
    );
    await page.goto(`/${role}`);
    await page
      .locator(".metrics")
      .getByRole("link")
      .filter({ has: page.getByText("Предложения", { exact: true }) })
      .click();
    await expect(page).toHaveURL(new RegExp(`/${role}/invitations$`));
    await page.goto(`/${role}`);
    await page
      .locator(".metrics")
      .getByRole("link")
      .filter({ hasText: "Непрочитанные сообщения" })
      .click();
    await expect(page).toHaveURL(new RegExp(`/${role}/conversations$`));
    await page.goto(`/${role}`);
    await page
      .locator(".metrics")
      .getByRole("link")
      .filter({
        hasText: role === "candidate" ? "Моя категория" : "Потребности команды",
      })
      .click();
    await expect(page).toHaveURL(
      new RegExp(`/${role}/${role === "candidate" ? "assessment" : "needs"}$`),
    );
  });
}

test("кандидат видит полученные достижения ФСП", async ({ page }) => {
  await page.route(
    (url) => url.pathname.startsWith("/api/"),
    async (route) => {
      const path = new URL(route.request().url()).pathname.replace(
        /^\/api/,
        "",
      );
      if (path === "/auth/refresh")
        return route.fulfill({
          json: { access_token: "test-only", token_type: "bearer" },
        });
      if (path === "/me")
        return route.fulfill({
          json: {
            id: userId,
            email: "fsp@example.com",
            roles: ["candidate"],
            email_verified: true,
          },
        });
      if (path === "/me/fsp")
        return route.fulfill({
          json: {
            verification_status: "verified",
            synced_at: "2026-10-11T00:00:00Z",
          },
        });
      if (path === "/me/profile")
        return route.fulfill({
          json: {
            fsp_achievements: [
              {
                id: "test-event",
                event_title: "Проверочный хакатон",
                place: 2,
                participants: 30,
                demo: true,
              },
            ],
          },
        });
      return route.fulfill({ json: [] });
    },
  );
  await page.goto("/candidate/fsp");
  await expect(
    page.getByRole("heading", { name: "Проверочный хакатон", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Место: 2 · Участников: 30", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Демонстрационное достижение", { exact: true }),
  ).toBeVisible();
});

test("банк тестов ограничивает выбор до запуска", async ({ page }) => {
  await page.route(
    (url) => url.pathname.startsWith("/api/"),
    async (route) => {
      const path = new URL(route.request().url()).pathname.replace(
        /^\/api/,
        "",
      );
      const payloads: Record<string, unknown> = {
        "/auth/refresh": { access_token: "test-only", token_type: "bearer" },
        "/me": {
          id: userId,
          email: "assessment@example.com",
          roles: ["candidate"],
          email_verified: true,
        },
        "/references/specializations": [
          { id: 1, code: "backend", name: "Backend", industry_id: 1 },
          { id: 2, code: "frontend", name: "Frontend", industry_id: 1 },
        ],
        "/references/grades": [
          { id: 1, code: "middle", name: "Middle", industry_id: 1 },
        ],
        "/surveys": [{ id: 1, title: "Опрос" }],
        "/surveys/1": { id: 1, industry_id: 1, questions: [] },
        "/me/surveys": [
          {
            id: "frontend-session",
            specialization_id: 2,
            claimed_grade_id: 1,
            finished_at: "2026-10-11T00:00:00Z",
          },
          {
            id: "backend-session",
            specialization_id: 1,
            claimed_grade_id: 1,
            finished_at: "2026-10-11T00:00:00Z",
          },
        ],
        "/test-capabilities": {
          mapping: { backend: "backend" },
          coverage: [
            { specialization: "backend", grade: "middle", has_templates: true },
          ],
        },
        "/me/profile": { skills: [] },
      };
      return route.fulfill({ json: payloads[path] ?? [] });
    },
  );
  await page.goto("/candidate/assessment");
  const selected = page.getByLabel("Завершённый опрос", { exact: true });
  await expect(
    selected.locator('option[value="frontend-session"]'),
  ).toHaveJSProperty("disabled", true);
  await expect(
    page.getByRole("button", { name: "Сформировать тест", exact: true }),
  ).toBeDisabled();
  await selected.selectOption("backend-session");
  await expect(
    page.getByRole("button", { name: "Сформировать тест", exact: true }),
  ).toBeEnabled();
});

test("путь к семантическому поиску виден работодателю", async ({ page }) => {
  const need = {
    id: "test-need",
    title: "Backend для команды",
    is_active: true,
    salary_min: 100000,
    salary_max: 200000,
    skills: [],
  };
  await page.route(
    (url) => url.pathname.startsWith("/api/"),
    async (route) => {
      const path = new URL(route.request().url()).pathname.replace(
        /^\/api/,
        "",
      );
      if (path === "/auth/refresh")
        return route.fulfill({
          json: { access_token: "test-only", token_type: "bearer" },
        });
      if (path === "/me")
        return route.fulfill({
          json: {
            id: userId,
            email: "search@example.com",
            roles: ["employer"],
            email_verified: true,
          },
        });
      if (path === "/needs") return route.fulfill({ json: [need] });
      if (path === `/needs/${need.id}`) return route.fulfill({ json: need });
      return route.fulfill({ json: [] });
    },
  );
  await page.goto("/employer/needs");
  await page
    .getByRole("link", { name: "Подобрать кандидатов", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Начать подбор", exact: false }),
  ).toBeInViewport();
});

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
