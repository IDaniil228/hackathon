import { test, expect } from "@playwright/test";
import { login } from "./session";
import { capture } from "./capture";

test("компания, потребность, публикация, отклик, короткое задание и ответ в чате", async ({
  page,
  browser,
}) => {
  const candidate = await browser.newPage();
  const account = await login(candidate, "candidate@scepka.example");
  await login(page, "employer@scepka.example");
  await page.goto("/employer/company");
  await page.getByRole("button", { name: "Сохранить компанию" }).click();
  await expect(
    page.getByText("Изменения сохранены", { exact: true }),
  ).toBeVisible();
  const categories = await (
    await page.request.get("/api/references/categories")
  ).json();
  const category = categories.find((row: any) => row.slug === "backend.middle");
  await page.goto("/employer/needs/new");
  await page
    .getByLabel("Название потребности")
    .fill("Сервисы обработки событий");
  await page
    .getByLabel("Кого ищете и чем занимается команда")
    .fill(
      "Команда развивает API и надёжную обработку событий. Ищем коллегу для работы с Python и PostgreSQL",
    );
  await page.getByRole("button", { name: "Создать потребность" }).click();
  await expect(page).toHaveURL(/\/employer\/needs\/[a-f0-9-]+$/);
  await capture(page, "need-editor-light", false);
  const title = `Backend для команды ${crypto.randomUUID().slice(0, 6)}`;
  await page.goto("/employer/vacancies/new");
  await page.getByLabel("Название вакансии").fill(title);
  await page
    .getByLabel("Категория", { exact: true })
    .selectOption(String(category.id));
  await page.getByLabel("Зарплата от, ₽").fill("180000");
  await page.getByLabel("Зарплата до, ₽").fill("280000");
  await page
    .getByLabel("Описание и условия")
    .fill(
      "Удалённая работа, код-ревью и понятные продуктовые задачи. Python, PostgreSQL, API",
    );
  await page.getByRole("button", { name: "Сохранить", exact: true }).click();
  await expect(page).toHaveURL(/\/employer\/vacancies\/[a-f0-9-]+$/);
  const vacancy = page.url().split("/").at(-1);
  await page.getByRole("button", { name: "Опубликовать", exact: true }).click();
  await expect(page.getByText("Опубликовано", { exact: true })).toBeVisible();
  await capture(page, "vacancy-editor-light");
  await candidate.goto(`/vacancies/${vacancy}`);
  await candidate
    .getByLabel("Сопроводительное письмо")
    .fill("Мне интересны надёжные сервисы и работа с данными");
  await candidate
    .getByRole("button", { name: "Откликнуться", exact: true })
    .click();
  await expect(
    candidate.getByText("Изменения сохранены", { exact: true }),
  ).toBeVisible();
  await page.goto("/employer/applications");
  await expect(
    page.getByRole("group", { name: title, exact: true }),
  ).toBeVisible();
  await capture(page, "applications-light", false);
  await page.goto(`/employer/candidates/${account.user_id}`);
  await page
    .getByRole("button", { name: "Короткое задание", exact: true })
    .click();
  await page.getByLabel("Название задания").fill("Хранение версий тарифов");
  await page
    .getByLabel("Условие", { exact: true })
    .fill(
      "Как сохранить тариф на момент заказа, если тарифы меняются? Предложите схему данных",
    );
  await page
    .getByRole("button", { name: "Выдать задание", exact: true })
    .click();
  await expect(page).toHaveURL(/\/employer\/assignments$/);
  await candidate.goto("/candidate/assignments");
  await candidate
    .getByRole("link", { name: "Хранение версий тарифов" })
    .first()
    .click();
  await candidate
    .getByLabel("Ваш подход или решение")
    .fill(
      "Сохраню версии тарифов с интервалами действия. Заказ будет ссылаться на конкретную версию",
    );
  await candidate.getByRole("button", { name: "Отправить ответ" }).click();
  await expect(
    candidate.getByText("Ответ получен", { exact: true }),
  ).toBeVisible();
  await capture(candidate, "assignment-answer-light");
  await page.goto("/employer/assignments");
  await page
    .getByRole("link", { name: "Хранение версий тарифов" })
    .first()
    .click();
  await page.getByLabel("Оценка", { exact: true }).fill("8");
  await page
    .getByLabel("Обратная связь")
    .fill("Понятный подход. Дополнительно обсудим перекрывающиеся интервалы");
  await page.getByRole("button", { name: "Сохранить", exact: true }).click();
  await expect(page.getByText("Оценено", { exact: true })).toBeVisible();
  await page.goto(`/employer/candidates/${account.user_id}`);
  await page.getByRole("button", { name: "Направить предложение" }).click();
  await page
    .getByLabel("Предложение и условия работы")
    .fill(
      "Приглашаем обсудить разработку API и версионирование данных в нашей команде",
    );
  await page.getByLabel("Зарплата от, ₽").fill("210000");
  await page.getByLabel("Зарплата до, ₽").fill("270000");
  await page.getByRole("button", { name: "Отправить предложение" }).click();
  await expect(page).toHaveURL(/\/employer\/conversations\//);
  const conversation = page.url().split("/").at(-1);
  await candidate.goto(`/candidate/conversations/${conversation}`);
  await candidate
    .getByLabel("Сообщение", { exact: true })
    .fill("Спасибо! Готова познакомиться с командой и обсудить задачи");
  await candidate
    .getByRole("button", { name: "Отправить", exact: true })
    .click();
  await expect(
    candidate.getByText(
      "Спасибо! Готова познакомиться с командой и обсудить задачи",
      { exact: true },
    ),
  ).toBeVisible();
  await candidate.close();
});
