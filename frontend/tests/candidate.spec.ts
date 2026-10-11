import { test, expect } from "@playwright/test";
import { capture } from "./capture";

test("регистрация, письмо, профиль, резюме, ФСП и настоящий опрос", async ({
  page,
  request,
}, info) => {
  const email = `frontend-${crypto.randomUUID()}@example.com`;
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/register?role=candidate");
  await page.getByLabel("Имя", { exact: true }).fill("Марта");
  await page.getByLabel("Фамилия", { exact: true }).fill("Пример");
  await page.getByLabel("Электронная почта").fill(email);
  await page
    .getByLabel("Пароль — от 12 символов")
    .fill("Frontend-demo-password-2026");
  await page.getByLabel("Согласен на обработку персональных данных").check();
  await page.getByRole("button", { name: "Создать аккаунт" }).click();
  await expect(page).toHaveURL(/verify-email/);
  await page.getByRole("button", { name: "Отправить письмо ещё раз" }).click();
  await expect(
    page.getByText("Письмо отправлено. Проверьте входящие"),
  ).toBeVisible();
  const mailbox = process.env.SCEPKA_MAILPIT_URL;
  if (!mailbox)
    throw new Error(
      "Задайте SCEPKA_MAILPIT_URL для проверки настоящего письма",
    );
  const messages = await (
    await request.get(`${mailbox}/api/v1/messages`)
  ).json();
  const message = messages.messages.find((m: any) =>
    m.To.some((to: any) => to.Address === email),
  );
  expect(message).toBeTruthy();
  const detail = await (
    await request.get(`${mailbox}/api/v1/message/${message.ID}`)
  ).json();
  const url = detail.Text.match(/https?:\/\/\S+/)?.[0];
  expect(url).toBeTruthy();
  await page.goto(url);
  await page.getByRole("button", { name: "Продолжить", exact: true }).click();
  await expect(page).toHaveURL(/\/candidate$/);
  await capture(page, "candidate-dashboard-light");
  await page.goto("/candidate/profile");
  await page.getByLabel("Город", { exact: true }).fill("Казань");
  await page.getByLabel("Опыт, месяцев").fill("18");
  await page
    .getByLabel("О себе и своём опыте")
    .fill(
      "Разрабатываю API на Python и PostgreSQL. Интересуют надёжные сервисы и работа с данными",
    );
  await page
    .getByRole("button", { name: "Сохранить", exact: true })
    .first()
    .click();
  await expect(
    page.getByText("Изменения сохранены", { exact: true }).first(),
  ).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("Город", { exact: true })).toHaveValue("Казань");
  await page.goto("/candidate/privacy");
  await page
    .getByRole("group", { name: "Синхронизация ФСП", exact: true })
    .getByRole("button", { name: "Разрешить" })
    .click();
  await expect(
    page
      .getByRole("group", { name: "Синхронизация ФСП", exact: true })
      .getByText("Согласие действует"),
  ).toBeVisible();
  await capture(page, "privacy-light");
  await page.goto("/candidate/fsp");
  await page
    .getByRole("button", { name: "Код для демо с достижением" })
    .click();
  await expect(page.getByLabel("Одноразовый код")).not.toHaveValue("");
  await page.getByRole("button", { name: "Привязать профиль" }).click();
  await expect(page.getByText("Привязана", { exact: true })).toBeVisible();
  await capture(page, "fsp-light");
  await page.goto("/candidate/documents");
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Мой PDF-профиль" }).click();
  const download = await downloadPromise;
  const path = info.outputPath("resume.pdf");
  await download.saveAs(path);
  await page.locator("input[type=file]").setInputFiles(path);
  await expect(
    page.getByRole("heading", { name: "Проверьте предложения" }),
  ).toBeVisible();
  await capture(page, "resume-parse-light");
  await page.goto("/candidate/assessment");
  await expect(page.getByLabel("Серверная логика и API")).toBeVisible();
  await page
    .getByLabel("Специализация", { exact: true })
    .selectOption({ label: "Backend-разработка" });
  await page
    .getByLabel("Предполагаемый грейд")
    .selectOption({ label: "Junior" });
  await page.getByLabel("Серверная логика и API").check();
  await page.getByLabel("PostgreSQL", { exact: true }).check();
  await page.getByLabel("Junior", { exact: true }).check();
  await page.getByRole("button", { name: "Сохранить опрос" }).click();
  await expect(
    page.getByText("Изменения сохранены", { exact: true }).first(),
  ).toBeVisible();
  await capture(page, "assessment-light");
  await page.getByLabel("Состав навыков").selectOption("general");
  const surveys = page.getByLabel("Завершённый опрос");
  const first = await surveys.locator("option").nth(1).getAttribute("value");
  await surveys.selectOption(first!);
  await page.getByRole("button", { name: "Сформировать тест" }).click();
  await expect(page).toHaveURL(/\/candidate\/tests\//, { timeout: 60000 });
  const terminal = await page.waitForResponse(
    async (response) => {
      if (
        !response.url().includes("/api/tests/") ||
        response.request().method() !== "GET" ||
        !response.ok()
      )
        return false;
      const data = await response.json();
      return ["ready", "failed"].includes(data.service_state);
    },
    { timeout: 150000 },
  );
  const state = await terminal.json();
  if (state.service_state === "failed") {
    expect(state.assigned_category_id).toBeNull();
    expect(state.result_applied).toBe(false);
    await expect(
      page.getByText("Не удалось сформировать проверенный набор", {
        exact: true,
      }),
    ).toBeVisible();
    await expect(page.locator(".cm-editor")).toHaveCount(0);
    await capture(page, "generation-failure-light", false);
  } else {
    expect(state.tasks_issued).toBe(true);
    expect(
      state.tasks.every((task: any) => task.public && task.statement),
    ).toBe(true);
    await expect(page.locator(".cm-editor")).toBeVisible();
    await expect(page.locator(".markdown").first()).not.toBeEmpty();
    await capture(page, "test-editor-light", false);
  }
  expect(errors).toEqual([]);
});
