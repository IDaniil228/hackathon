import { useState, lazy, Suspense, useEffect } from "react";
import { useNavigate, useParams, useLocation, Link } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  Check,
  FileDown,
  ShieldCheck,
  Upload,
  GitBranch,
  Plus,
} from "lucide-react";
import { useSession } from "./session";
import { request, send, download } from "./api/client";
import { action as submitAction, updateOperation } from "./api/actions";
import type { components } from "./api/schema";
import Markdown from "./Markdown";
import { Brand, Theme, Federation } from "./App";
import {
  Action,
  Badge,
  Button,
  Data,
  Empty,
  ErrorBox,
  Form,
  Go,
  Heading,
  ListRow,
  Loading,
  Panel,
  RefSelect,
  date,
  formatOptions,
  options,
  text,
  useData,
  type Row,
} from "./ui";

const Editor = lazy(() => import("./Editor"));
export const invalidate = (client: ReturnType<typeof useQueryClient>) =>
  client.invalidateQueries();
export function useReferences() {
  const specs = useData<Row[]>("/references/specializations");
  const grades = useData<Row[]>("/references/grades");
  const cats = useData<Row[]>("/references/categories");
  const skills = useData<Row[]>("/references/skills");
  return {
    specs: specs.data || [],
    grades: grades.data || [],
    cats: cats.data || [],
    skills: skills.data || [],
  };
}
export function Auth({ mode }: { mode: string }) {
  const session = useSession();
  const navigate = useNavigate();
  const location = useLocation();
  const [role, setRole] = useState(
    new URLSearchParams(location.search).get("role") || "candidate",
  );
  const [notice, setNotice] = useState("");
  const [demoToken, setDemoToken] = useState("");
  const isRegister = mode === "register";
  const titles: Record<string, string> = {
    login: "Рады видеть вас снова",
    register: "Начнём со знакомства",
    "forgot-password": "Вернём доступ",
    "reset-password": "Новый пароль",
    "verify-email": "Подтвердите почту",
  };
  const fields =
    mode === "verify-email"
      ? [text("token", "Код из письма", true)]
      : mode === "forgot-password"
        ? [text("email", "Электронная почта", true, "email")]
        : mode === "reset-password"
          ? [
              text("token", "Код из письма", true),
              text(
                "password",
                "Новый пароль — от 12 символов",
                true,
                "password",
              ),
            ]
          : [
              ...(isRegister
                ? [
                    text("first_name", "Имя", true),
                    text("last_name", "Фамилия", true),
                  ]
                : []),
              text("email", "Электронная почта", true, "email"),
              text(
                "password",
                isRegister ? "Пароль — от 12 символов" : "Пароль",
                true,
                "password",
              ),
              ...(isRegister
                ? [
                    text(
                      "accept_processing",
                      "Согласен на обработку персональных данных",
                      true,
                      "checkbox",
                    ),
                  ]
                : []),
            ];
  return (
    <div className="auth-page">
      <nav className="public-nav">
        <Brand />
        <Theme />
      </nav>
      <main className="auth-grid">
        <div className="auth-story">
          <p className="eyebrow">Подтверждённые связи</p>
          <h1>
            Ваш следующий шаг
            <br />
            <span>начинается здесь</span>
          </h1>
          <p>
            Не громкие слова в резюме, а навыки, которые можно проверить. Не
            случайный отклик, а предложение с понятными условиями
          </p>
          <div className="auth-art">
            <GitBranch size={110} />
          </div>
          <Federation />
        </div>
        <Panel className="auth-card">
          <Heading title={titles[mode]}>
            {isRegister
              ? "Один аккаунт может объединять оба кабинета"
              : "Ваш профиль и все диалоги останутся на месте"}
          </Heading>
          {isRegister && (
            <div className="segmented">
              {[
                ["candidate", "Кандидат"],
                ["employer", "Работодатель"],
                ["both", "Обе роли"],
              ].map(([r, label]) => (
                <button
                  aria-pressed={r === role}
                  key={r}
                  onClick={() => setRole(r)}
                >
                  {label}
                </button>
              ))}
            </div>
          )}
          <Form
            key={mode + demoToken}
            fields={fields}
            initial={{
              token:
                demoToken ||
                location.state?.generatedToken ||
                new URLSearchParams(location.search).get("token"),
            }}
            label={
              mode === "login"
                ? "Войти"
                : isRegister
                  ? "Создать аккаунт"
                  : "Продолжить"
            }
            submit={async (v) => {
              if (mode === "login") {
                await session.login(v.email, v.password);
                const me = await request("/me");
                navigate(
                  `/${me.roles.includes("candidate") ? "candidate" : "employer"}`,
                );
              } else if (isRegister) {
                await send("/auth/register", {
                  ...v,
                  roles: role === "both" ? ["candidate", "employer"] : [role],
                });
                await session.login(v.email, v.password);
                let deliveryNotice = "Письмо отправлено. Проверьте входящие";
                let generatedToken = "";
                try {
                  const delivery = await send("/auth/email/request");
                  if (delivery.demo) {
                    deliveryNotice =
                      "Демонстрационный код: письмо наружу не отправлено";
                    generatedToken = delivery.token;
                  }
                } catch {
                  deliveryNotice =
                    "Аккаунт создан, но письмо не отправлено. Запросите его повторно на этой странице";
                }
                navigate("/verify-email", {
                  state: { deliveryNotice, generatedToken },
                });
              } else if (mode === "verify-email") {
                await send("/auth/email/verify", v);
                await session.reload();
                navigate(`/${session.user?.roles[0] || "candidate"}`);
              } else if (mode === "forgot-password") {
                await send("/auth/password/request", v);
                setNotice(
                  "Если аккаунт существует, письмо с инструкцией уже отправлено",
                );
              } else {
                await send("/auth/password/reset", v);
                navigate("/login");
              }
            }}
          />
          {(notice ||
            (mode === "verify-email" && location.state?.deliveryNotice)) && (
            <p role="status" className="notice">
              {notice || location.state?.deliveryNotice}
            </p>
          )}
          {mode === "verify-email" && (
            <Action
              secondary
              run={async () => {
                const result = await send("/auth/email/request");
                if (result.demo) {
                  setDemoToken(result.token);
                  setNotice(
                    "Демонстрационный код: письмо наружу не отправлено",
                  );
                } else setNotice("Письмо отправлено. Проверьте входящие");
              }}
            >
              Отправить письмо ещё раз
            </Action>
          )}
          <div className="auth-links">
            <Link to={isRegister ? "/login" : "/register"}>
              {isRegister
                ? "Уже есть аккаунт? Войти"
                : "Ещё нет аккаунта? Зарегистрироваться"}
            </Link>
            {mode === "login" && (
              <Link to="/forgot-password">Не помню пароль</Link>
            )}
          </div>
        </Panel>
      </main>
    </div>
  );
}
export function Dashboard({ role }: { role: "candidate" | "employer" }) {
  const session = useSession();
  const profile = useData("/me/profile", undefined, role === "candidate");
  const needs = useData("/needs", undefined, role === "employer");
  const invitations = useData(`/invitations?cabinet=${role}`);
  const conversations = useData("/conversations");
  const unread =
    conversations.data?.results?.reduce(
      (n: number, r: Row) => n + r.unread_count,
      0,
    ) || 0;
  return (
    <>
      <Heading
        eyebrow={
          role === "candidate" ? "Ваш следующий шаг" : "Рабочее пространство"
        }
        title={
          role === "candidate"
            ? `Здравствуйте${profile.data?.first_name ? ", " + profile.data.first_name : ""}`
            : "Сильная команда начинается здесь"
        }
      >
        {role === "candidate"
          ? "Ваш опыт, подтверждённые навыки и новые возможности — в одном месте"
          : "Опишите потребность. Найдите людей. Начните с конкретного предложения"}
      </Heading>
      <div className="dashboard-banner">
        <div>
          <span className="eyebrow">
            {role === "candidate"
              ? "Профиль сильнее слов"
              : "Точность вместо случайности"}
          </span>
          <h2>
            {role === "candidate"
              ? "Покажите, что умеете"
              : "Найдите точку сцепления"}
          </h2>
          <p>
            {role === "candidate"
              ? "Подтверждённый уровень помогает работодателю заметить ваш профиль"
              : "Ищите по навыкам и смыслу. Уточняйте запрос, сохраняя предыдущую подборку"}
          </p>
          <Link
            to={`/${role}/${role === "candidate" ? "assessment" : "needs/new"}`}
            className="button"
          >
            {role === "candidate"
              ? "Подтвердить навыки"
              : "Создать потребность"}
            <ArrowRight size={17} />
          </Link>
        </div>
        <GitBranch className="banner-glyph" size={150} />
      </div>
      <div className="three-grid metrics">
        <Link
          className="panel"
          to={`/${role}/${role === "candidate" ? "assessment" : "needs"}`}
        >
          <span className="metric-label">
            {role === "candidate" ? "Моя категория" : "Потребности команды"}
          </span>
          <strong className="metric">
            {role === "candidate"
              ? profile.data?.category?.name || "Первый шаг"
              : (needs.data?.length ?? "—")}
          </strong>
          <p>
            {role === "candidate"
              ? profile.data?.grade_confirmed
                ? "Подтверждена тестом"
                : "Пройдите опрос и тестирование"
              : "Сохранённые направления поиска"}
          </p>
        </Link>
        <Link className="panel" to={`/${role}/invitations`}>
          <span className="metric-label">Предложения</span>
          <strong className="metric figure">
            {invitations.data?.length ?? "—"}
          </strong>
          <p>Условия и зарплата до начала общения</p>
        </Link>
        <Link className="panel" to={`/${role}/conversations`}>
          <span className="metric-label">Непрочитанные сообщения</span>
          <strong className="metric figure">{unread}</strong>
          <p>Каждый разговор начинается с предложения</p>
        </Link>
      </div>
      <div className="two-grid">
        <Panel>
          <h3>Быстрые переходы</h3>
          {(role === "candidate"
            ? [
                [
                  "profile",
                  "Дополнить профиль",
                  "Опыт, навыки и ваши предпочтения",
                ],
                [
                  "documents",
                  "Загрузить резюме",
                  "Перенос полей после вашего подтверждения",
                ],
                [
                  "privacy",
                  "Проверить видимость",
                  "Вы решаете, что увидит работодатель",
                ],
              ]
            : [
                [
                  "candidates",
                  "Исследовать банк",
                  "Категории и проверенные навыки",
                ],
                [
                  "invitations",
                  "Посмотреть предложения",
                  "История взаимодействия с кандидатами",
                ],
                [
                  "company",
                  "Обновить компанию",
                  "Представьте команду будущим коллегам",
                ],
              ]
          ).map(([to, title, desc]) => (
            <ListRow
              key={to}
              to={`/${role}/${to}`}
              title={title}
              subtitle={desc}
            />
          ))}
        </Panel>
        <Panel>
          <p className="eyebrow">Прозрачные правила</p>
          <h3>
            Сначала условия,
            <br />
            затем разговор
          </h3>
          <p>
            Зарплата указана в рублях за месяц до вычета НДФЛ. Переписка не
            раскрывает контакты автоматически
          </p>
          <div className="trust-line">
            <ShieldCheck size={24} />
            <span>Контроль остаётся у кандидата</span>
          </div>
          {session.user?.demo_auth && (
            <p className="notice">Демонстрационное окружение</p>
          )}
        </Panel>
      </div>
    </>
  );
}
export function Profile() {
  const q = useData("/me/profile");
  const refs = useReferences();
  const qc = useQueryClient();
  const [skill, setSkill] = useState("");
  const [level, setLevel] = useState("3");
  const fields = [
    text("first_name", "Имя", true),
    text("last_name", "Фамилия", true),
    text("middle_name", "Отчество"),
    text("birth_date", "Дата рождения", false, "date"),
    text("phone", "Телефон"),
    text("city", "Город"),
    text("experience_months", "Опыт, месяцев", true, "number"),
    options("work_format", "Формат работы", formatOptions),
    options("employment_type", "Занятость", [
      ["full_time", "Полная"],
      ["part_time", "Частичная"],
      ["project", "Проектная"],
      ["internship", "Стажировка"],
    ]),
    text("relocation", "Готов к переезду", false, "checkbox"),
    text("is_open_to_offers", "Открыт к предложениям", false, "checkbox"),
    text("about", "О себе и своём опыте", false, "textarea"),
  ];
  return (
    <>
      <Heading eyebrow="Профиль кандидата" title="Покажите, что вас отличает">
        Заявленные навыки и подтверждённые результаты отображаются отдельно
      </Heading>
      <Data query={q}>
        {(r) => (
          <>
            <Panel>
              <h3>Основная информация</h3>
              <Form
                key={r.user_id}
                fields={fields}
                initial={r.profile}
                submit={async (v) => {
                  await send("/me/profile", v, "PATCH");
                  await invalidate(qc);
                }}
              />
            </Panel>
            <Panel>
              <h3>Ваши предпочтения</h3>
              <Form
                fields={[
                  RefSelect(
                    "desired_specialization_id",
                    "Направление",
                    refs.specs,
                  ),
                  text("salary_min", "Зарплата от, ₽", false, "number"),
                  text("salary_max", "Зарплата до, ₽", false, "number"),
                  text(
                    "positive_prompt",
                    "Что хотелось бы найти",
                    false,
                    "textarea",
                  ),
                  text("negative_prompt", "Что не подходит", false, "textarea"),
                ]}
                initial={r.preferences || {}}
                submit={async (v) => {
                  await send("/me/preferences", v, "PUT");
                  await invalidate(qc);
                }}
              >
                <small>В рублях за месяц до вычета НДФЛ</small>
              </Form>
            </Panel>
            <Panel>
              <h3>Навыки</h3>
              <div className="skill-list">
                {r.skills.map((s: Row) => (
                  <div className="skill-pill" key={s.code}>
                    <span>{s.name}</span>
                    <small>Заявлено: {s.self_level}/5</small>
                    {s.confirmed_level != null && (
                      <strong>
                        <Check size={13} /> {s.confirmed_level}/5
                      </strong>
                    )}
                  </div>
                ))}
              </div>
              <div className="inline-form">
                <label>
                  Навык
                  <select
                    value={skill}
                    onChange={(e) => setSkill(e.target.value)}
                  >
                    <option value="">Выберите навык</option>
                    {refs.skills.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Ваш уровень
                  <select
                    value={level}
                    onChange={(e) => setLevel(e.target.value)}
                  >
                    {[0, 1, 2, 3, 4, 5].map((n) => (
                      <option key={n}>{n}</option>
                    ))}
                  </select>
                </label>
                <Action
                  run={async () => {
                    if (!skill) throw new Error("Выберите навык");
                    await send(
                      "/me/skills",
                      {
                        skills: [
                          {
                            skill_id: Number(skill),
                            self_level: Number(level),
                          },
                        ],
                      },
                      "PUT",
                    );
                    await invalidate(qc);
                  }}
                >
                  <Plus size={16} />
                  Добавить или обновить
                </Action>
              </div>
            </Panel>
            <Panel>
              <h3>Ссылки и портфолио</h3>
              {r.links.map((l: Row) => (
                <ListRow key={l.id} title={l.label || l.kind} subtitle={l.url}>
                  <Action
                    secondary
                    run={async () => {
                      await request(`/me/links/${l.id}`, { method: "DELETE" });
                      await invalidate(qc);
                    }}
                  >
                    Удалить
                  </Action>
                </ListRow>
              ))}
              <Form
                fields={[
                  options("kind", "Тип ссылки", [
                    ["github", "GitHub"],
                    ["portfolio", "Портфолио"],
                    ["telegram", "Telegram"],
                    ["other", "Другая"],
                  ]),
                  text("label", "Подпись"),
                  text("url", "Адрес ссылки", true, "url"),
                  text("is_public", "Показывать публично", false, "checkbox"),
                ]}
                label="Добавить ссылку"
                submit={async (v) => {
                  await send("/me/links", v);
                  await invalidate(qc);
                }}
              />
            </Panel>
          </>
        )}
      </Data>
    </>
  );
}
const privacyLabels: Record<string, string> = {
  is_searchable: "Профиль участвует в поиске",
  show_contacts: "Разрешить контакты после принятия предложения или отклика",
  show_salary: "Зарплатные ожидания",
  show_fsp: "Достижения ФСП",
  show_github: "GitHub",
  show_name: "Имя и фамилия",
  show_city: "Город и формат работы",
  show_experience: "Опыт работы",
  show_about: "Текст о себе",
};
const consentLabels: Record<string, string> = {
  pd_processing: "Обработка персональных данных",
  profile_publication: "Публикация профиля",
  fsp_data_sync: "Синхронизация ФСП",
};
export function Privacy() {
  const q = useData("/me/profile");
  const consents = useData("/me/consents");
  const disclosures = useData("/me/disclosures");
  const qc = useQueryClient();
  const session = useSession();
  return (
    <>
      <Heading eyebrow="Контроль доступа" title="Ваш профиль — ваши правила">
        Скрытые поля недоступны в карточке, подборке и PDF работодателя
      </Heading>
      <Panel>
        <h3>Согласия</h3>
        <Data query={consents}>
          {(rows) =>
            Object.entries(consentLabels).map(([kind, title]) => {
              const granted = rows.some(
                (r: Row) => r.kind === kind && !r.revoked_at,
              );
              return (
                <ListRow
                  key={kind}
                  title={title}
                  subtitle={
                    granted ? "Согласие действует" : "Согласие не выдано"
                  }
                >
                  <Action
                    secondary
                    confirm={
                      granted
                        ? "Отозвать согласие? Это может скрыть профиль или достижения"
                        : undefined
                    }
                    run={async () => {
                      await send("/me/consents", { kind, granted: !granted });
                      await qc.resetQueries();
                    }}
                  >
                    {granted ? "Отозвать" : "Разрешить"}
                  </Action>
                </ListRow>
              );
            })
          }
        </Data>
      </Panel>
      <Panel>
        <h3>Что видит работодатель</h3>
        <Data query={q}>
          {(r) => (
            <Form
              key={r.privacy.updated_at}
              fields={Object.entries(privacyLabels).map(([key, title]) =>
                text(key, title, false, "checkbox"),
              )}
              initial={r.privacy}
              submit={async (v) => {
                await send("/me/privacy", v, "PUT");
                await qc.resetQueries();
              }}
            />
          )}
        </Data>
      </Panel>
      {session.user?.public_contacts_enabled && (
        <Panel>
          <h3>Публичные контакты — экспериментальный режим</h3>
          <p>
            Этот режим раскрывает контакты без принятого приглашения. Он
            отличается от стандартных правил платформы
          </p>
          <Form
            fields={[
              text(
                "public_contacts",
                "Показывать контакты публично",
                false,
                "checkbox",
              ),
              text(
                "accept_public_contacts",
                "Я отдельно согласен на публичное раскрытие",
                false,
                "checkbox",
              ),
            ]}
            initial={q.data?.privacy}
            submit={async (v) => {
              await send("/me/privacy", v, "PUT");
              await qc.resetQueries();
            }}
          />
        </Panel>
      )}
      <Panel>
        <h3>Компании с доступом к контактам</h3>
        <Data query={disclosures}>
          {(rows) =>
            rows.length ? (
              rows.map((r: Row) => (
                <ListRow
                  key={r.company_id}
                  title={r.company_name}
                  subtitle={date(r.disclosed_at)}
                >
                  <Action
                    secondary
                    run={async () => {
                      await send(`/me/disclosures/${r.company_id}/revoke`);
                      await qc.resetQueries();
                    }}
                  >
                    Отозвать доступ
                  </Action>
                </ListRow>
              ))
            ) : (
              <Empty title="Контакты пока не раскрыты" />
            )
          }
        </Data>
      </Panel>
    </>
  );
}
export function Documents() {
  const q = useData("/me/resumes");
  const qc = useQueryClient();
  const refs = useReferences();
  const [parsed, setParsed] = useState<Row>();
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [skills, setSkills] = useState<number[]>([]);
  return (
    <>
      <Heading
        eyebrow="Документы"
        title="Резюме без повторного ввода"
        action={
          <Action secondary run={() => download("/me/profile.pdf")}>
            <FileDown size={17} />
            Мой PDF-профиль
          </Action>
        }
      >
        Загрузите PDF, проверьте предложения и перенесите только нужные поля
      </Heading>
      <Panel>
        <label className="upload-zone">
          <Upload size={28} />
          <strong>{busy ? "Читаем резюме…" : "Выберите PDF-резюме"}</strong>
          <span>
            Текстовый PDF · до 20 MiB · сканы без OCR не поддерживаются
          </span>
          <input
            type="file"
            accept="application/pdf"
            disabled={busy}
            onChange={async (e) => {
              const file = e.target.files?.[0];
              if (!file) return;
              const body = new FormData();
              body.append("file", file);
              setBusy(true);
              setError(undefined);
              try {
                const result = await request("/me/resumes", {
                  method: "POST",
                  body,
                });
                setParsed(result.parse);
                setSelected([]);
                setSkills([]);
                await invalidate(qc);
              } catch (e) {
                setError(e);
              } finally {
                setBusy(false);
              }
            }}
          />
        </label>
        <ErrorBox error={error} />
      </Panel>
      {parsed && (
        <Panel>
          <h3>Проверьте предложения</h3>
          <p>
            {parsed.payload.extraction_verified
              ? "Перенос проверен по исходному тексту"
              : "Распознавание по текстовым правилам"}{" "}
            · Это не подтверждает грейд или навыки
          </p>
          {Object.entries(parsed.payload.fields).map(([key, value]) => (
            <label key={key} className="proposal">
              <input
                type="checkbox"
                checked={selected.includes(key)}
                onChange={(e) =>
                  setSelected(
                    e.target.checked
                      ? [...selected, key]
                      : selected.filter((x) => x !== key),
                  )
                }
              />
              <span>
                {key}
                <strong>{String(value)}</strong>
              </span>
            </label>
          ))}
          {parsed.payload.skill_ids.map((id: number) => (
            <label key={id} className="proposal">
              <input
                type="checkbox"
                checked={skills.includes(id)}
                onChange={(e) =>
                  setSkills(
                    e.target.checked
                      ? [...skills, id]
                      : skills.filter((x) => x !== id),
                  )
                }
              />
              {refs.skills.find((s) => s.id === id)?.name || id}
            </label>
          ))}
          <Action
            run={async () => {
              await send(`/me/resume-parses/${parsed.id}/apply`, {
                accept_fields: selected,
                accept_skill_ids: skills,
              });
              setParsed(undefined);
              await invalidate(qc);
            }}
          >
            Применить выбранное
          </Action>
        </Panel>
      )}
      <Panel>
        <h3>Мои документы</h3>
        <Data query={q}>
          {(rows) =>
            rows.length ? (
              rows.map((r: Row) => (
                <ListRow
                  key={r.id}
                  title={r.file_name}
                  subtitle={date(r.created_at)}
                >
                  <Action
                    secondary
                    run={() => download(`/me/resumes/${r.id}`, r.file_name)}
                  >
                    Скачать
                  </Action>
                  <Action
                    secondary
                    run={async () => {
                      setParsed(await request(`/me/resumes/${r.id}/parse`));
                      setSelected([]);
                      setSkills([]);
                    }}
                  >
                    Проверить распознавание
                  </Action>
                </ListRow>
              ))
            ) : (
              <Empty title="Резюме ещё не загружено" />
            )
          }
        </Data>
      </Panel>
    </>
  );
}
export function Fsp() {
  const q = useData("/me/fsp");
  const profile = useData("/me/profile");
  const qc = useQueryClient();
  const [code, setCode] = useState("");
  return (
    <>
      <Heading
        eyebrow="История достижений"
        title="Спортивный опыт имеет значение"
      >
        Демонстрационная интеграция: реальный реестр и вход через FSP ID пока не
        подключены
      </Heading>
      <Panel>
        <div className="trust-line">
          <FingerprintIcon />
          <div>
            <h3>Демонстрационный реестр ФСП</h3>
            <p>
              Отсутствие истории — нормальный сценарий. Оно не означает низкую
              квалификацию
            </p>
          </div>
        </div>
        <Data query={q}>
          {(r) => (
            <>
              <p>
                Связь:{" "}
                <strong>
                  {r.verification_status === "verified"
                    ? "Привязана"
                    : "Не установлена"}
                </strong>
              </p>
              <p>Последняя синхронизация: {date(r.synced_at)}</p>
            </>
          )}
        </Data>
        <div className="actions">
          <Action
            secondary
            run={async () => {
              const r = await send("/me/fsp/demo-code", {
                history: "team_prize",
              });
              setCode(r.code);
            }}
          >
            Код для демо с достижением
          </Action>
          <Action
            secondary
            run={async () => {
              const r = await send("/me/fsp/demo-code", { history: "empty" });
              setCode(r.code);
            }}
          >
            Код без истории
          </Action>
        </div>
        <Form
          key={code}
          fields={[text("code", "Одноразовый код", true)]}
          initial={{ code }}
          label="Привязать профиль"
          submit={async (v) => {
            await send("/me/fsp/link", v);
            await invalidate(qc);
          }}
        />
        <div className="actions">
          <Action
            secondary
            run={async () => {
              await send("/me/fsp/sync");
              await invalidate(qc);
            }}
          >
            Синхронизировать
          </Action>
          <Action
            secondary
            confirm="Отвязать ФСП и удалить сигналы реестра?"
            run={async () => {
              await request("/me/fsp/link", { method: "DELETE" });
              await qc.resetQueries();
            }}
          >
            Отвязать
          </Action>
        </div>
        <p>
          <Link to="/candidate/privacy">
            Для привязки сначала разрешите синхронизацию в настройках
            приватности →
          </Link>
        </p>
      </Panel>
      <Panel>
        <h3>Достижения в профиле</h3>
        <Data query={profile}>
          {(r) =>
            r.fsp_achievements?.length ? (
              r.fsp_achievements.map((achievement: Row) => (
                <article className="list-row" key={achievement.id}>
                  <div>
                    <h4>{achievement.event_title}</h4>
                    <p>
                      Место: {achievement.place ?? "Не указано"} · Участников:{" "}
                      {achievement.participants ?? "Не указано"}
                    </p>
                    {achievement.team_name && (
                      <p>Команда: {achievement.team_name}</p>
                    )}
                    {achievement.demo && (
                      <p className="notice">Демонстрационное достижение</p>
                    )}
                  </div>
                </article>
              ))
            ) : (
              <Empty title="Достижений пока нет">
                После привязки профиля и синхронизации здесь появится полученная
                история
              </Empty>
            )
          }
        </Data>
        <p>
          Работодатель увидит разрешённые достижения в вашей карточке. Они
          учитываются как фактор ФСП при подборе; грейд подтверждается
          тестированием
        </p>
      </Panel>
    </>
  );
}
function FingerprintIcon() {
  return <ShieldCheck size={38} />;
}
export function Operation({
  id,
  done,
}: {
  id: string;
  done?: (r: Row) => void;
}) {
  const q = useData<components["schemas"]["OperationOut"]>(
    `/operations/${id}`,
    1500,
  );
  const terminal = ["failed", "uncertain", "succeeded"];
  useEffect(() => {
    if (q.data) updateOperation(q.data);
    if (q.data?.state === "succeeded") done?.(q.data);
  }, [q.data?.state, id]);
  return (
    <Data query={q}>
      {(r) => (
        <div className="operation">
          <Badge value={r.state} />
          {!terminal.includes(r.state) && (
            <span>Состояние обновляется автоматически</span>
          )}
          {r.error && <ErrorBox error={r.error} />}
          {r.state === "uncertain" && (
            <p>Не запускайте повтор с новым ключом. Результат требует сверки</p>
          )}
        </div>
      )}
    </Data>
  );
}
export function Assessment() {
  const refs = useReferences();
  const surveys = useData("/surveys");
  const history = useData("/me/surveys");
  const tests = useData("/tests", 5000);
  const grades = useData("/me/grades");
  const caps = useData("/test-capabilities");
  const profile = useData("/me/profile");
  const [surveyId, setSurveyId] = useState<number>();
  const survey = useData(`/surveys/${surveyId}`, undefined, !!surveyId);
  const [answers, setAnswers] = useState<Row>({});
  const [operation, setOperation] = useState("");
  const [skillMode, setSkillMode] = useState("all");
  const [chosen, setChosen] = useState<number[]>([]);
  const [testSurveyId, setTestSurveyId] = useState("");
  const available = (row: Row) => {
    const specialization = refs.specs.find(
      (s) => s.id === row.specialization_id,
    )?.code;
    const grade = refs.grades.find((g) => g.id === row.claimed_grade_id)?.code;
    return !!caps.data?.coverage?.some(
      (c: Row) =>
        c.specialization === specialization &&
        c.grade === grade &&
        c.has_templates,
    );
  };
  const testSurvey = history.data?.find((r: Row) => r.id === testSurveyId);
  const navigate = useNavigate();
  const qc = useQueryClient();
  useEffect(() => {
    if (!surveyId && surveys.data?.length) setSurveyId(surveys.data[0].id);
  }, [surveys.data]);
  return (
    <>
      <Heading
        eyebrow="Подтверждение компетенций"
        title="Навыки, за которыми стоит результат"
      >
        Опрос → заявленный уровень → индивидуальный тест → подтверждённая
        категория
      </Heading>
      <div className="notice">
        Смена подтверждённого грейда ограничена по времени. Провал не понижает
        уже подтверждённую категорию
      </div>
      <Panel>
        <h3>Входной опрос</h3>
        <Data query={surveys}>
          {(rows) => (
            <label className="field">
              Опрос
              <select
                value={surveyId || ""}
                onChange={(e) => {
                  setSurveyId(Number(e.target.value));
                  setAnswers({});
                }}
              >
                {rows.map((r: Row) => (
                  <option value={r.id} key={r.id}>
                    {r.title || r.name || r.code}
                  </option>
                ))}
              </select>
            </label>
          )}
        </Data>
        <Data query={survey}>
          {(r) => (
            <Form
              fields={[
                RefSelect(
                  "specialization_id",
                  "Специализация",
                  refs.specs.filter((s) => s.industry_id === r.industry_id),
                  true,
                ),
                RefSelect(
                  "claimed_grade_id",
                  "Предполагаемый грейд",
                  refs.grades.filter((g) => g.industry_id === r.industry_id),
                  true,
                ),
              ]}
              label="Сохранить опрос"
              submit={async (v) => {
                const saved = await send(`/surveys/${r.id}/submit`, {
                  ...v,
                  answers: r.questions.map((q: Row) => ({
                    question_id: q.id,
                    option_ids: Array.isArray(answers[q.id])
                      ? answers[q.id]
                      : [],
                    free_text:
                      typeof answers[q.id] === "string" ? answers[q.id] : null,
                  })),
                });
                await invalidate(qc);
                setTestSurveyId(saved.id);
              }}
            >
              {r.questions.map((q: Row) => (
                <fieldset key={q.id} className="question">
                  <legend>
                    {q.text || q.title || q.question_text}
                    {q.is_required && " *"}
                  </legend>
                  {q.options.length ? (
                    q.options.map((o: Row) => (
                      <label key={o.id} className="choice">
                        <input
                          type={
                            q.format === "single_choice" ? "radio" : "checkbox"
                          }
                          name={`q-${q.id}`}
                          onChange={(e) =>
                            setAnswers({
                              ...answers,
                              [q.id]:
                                q.format === "single_choice"
                                  ? [o.id]
                                  : e.target.checked
                                    ? [...(answers[q.id] || []), o.id]
                                    : (answers[q.id] || []).filter(
                                        (n: number) => n !== o.id,
                                      ),
                            })
                          }
                        />
                        {o.text || o.label || o.option_text}
                      </label>
                    ))
                  ) : (
                    <textarea
                      aria-label={q.text || q.title || "Ответ на вопрос"}
                      onChange={(e) =>
                        setAnswers({ ...answers, [q.id]: e.target.value })
                      }
                      required={q.is_required}
                    />
                  )}
                </fieldset>
              ))}
            </Form>
          )}
        </Data>
      </Panel>
      <Panel>
        <h3>Начать тестирование</h3>
        <Data query={caps}>
          {(r) => (
            <p className="muted">
              Подключены:{" "}
              {Object.keys(r.mapping)
                .map(
                  (code) =>
                    refs.specs.find((s) => s.code === code)?.name || code,
                )
                .join(", ")}
              . Наличие шаблонов не гарантирует достаточный набор заданий
            </p>
          )}
        </Data>
        <label className="field">
          Состав навыков
          <select
            value={skillMode}
            onChange={(e) => setSkillMode(e.target.value)}
          >
            <option value="all">Все заявленные навыки</option>
            <option value="general">Общий тест направления</option>
            <option value="selected">Выбрать навыки</option>
          </select>
        </label>
        {skillMode === "selected" &&
          profile.data?.skills.map((s: Row) => {
            const id = refs.skills.find((x) => x.code === s.code)?.id;
            return (
              id && (
                <label className="choice" key={id}>
                  <input
                    type="checkbox"
                    onChange={(e) =>
                      setChosen(
                        e.target.checked
                          ? [...chosen, id]
                          : chosen.filter((x) => x !== id),
                      )
                    }
                  />
                  {s.name}
                </label>
              )
            );
          })}
        <Form
          key={testSurveyId}
          initial={{ survey_session_id: testSurveyId }}
          disabled={!testSurvey || !available(testSurvey)}
          onChange={(event) =>
            setTestSurveyId(
              String(
                new FormData(event.currentTarget).get("survey_session_id") ||
                  "",
              ),
            )
          }
          fields={[
            RefSelect(
              "survey_session_id",
              "Завершённый опрос",
              (history.data || []).map((r: Row) => ({
                id: r.id,
                name: `${date(r.finished_at)} · ${refs.specs.find((s) => s.id === r.specialization_id)?.name || ""}${available(r) ? "" : " · банк не подключён"}`,
                disabled: !available(r),
              })),
              true,
            ),
          ]}
          label="Сформировать тест"
          submit={async (v) => {
            const result = await submitAction("/tests", {
              survey_session_id: v.survey_session_id,
              skill_ids:
                skillMode === "all"
                  ? null
                  : skillMode === "general"
                    ? []
                    : chosen,
            });
            setOperation(result.id);
          }}
        />
        {caps.data && !testSurvey && (
          <p className="muted">
            Выберите завершённый опрос с подключённым банком. Недоступные
            направления отмечены в списке
          </p>
        )}
        {testSurvey && !available(testSurvey) && (
          <p className="notice">
            Опрос сохранён, но для выбранного направления и грейда банк тестов
            пока не подключён. Выберите поддерживаемое направление во входном
            опросе
          </p>
        )}
        {operation && (
          <Operation
            id={operation}
            done={(r) => navigate(`/candidate/tests/${r.resource_id}`)}
          />
        )}
      </Panel>
      <Panel>
        <h3>Мои попытки</h3>
        <Data query={tests}>
          {(rows) =>
            rows.length ? (
              rows.map((r: Row) => (
                <ListRow
                  key={r.id}
                  title={
                    refs.specs.find((s) => s.id === r.specialization_id)
                      ?.name || "Тестирование"
                  }
                  subtitle={date(r.created_at)}
                  to={`/candidate/tests/${r.id}`}
                  status={r.service_state}
                />
              ))
            ) : (
              <Empty title="Первый тест ещё впереди" />
            )
          }
        </Data>
      </Panel>
      <Panel>
        <h3>История категории</h3>
        <Data query={grades}>
          {(rows) =>
            rows.length ? (
              rows.map((r: Row) => (
                <ListRow
                  key={r.id}
                  title={
                    refs.cats.find((c) => c.id === r.category_id)?.name ||
                    "Категория"
                  }
                  subtitle={`${r.source === "test" ? "Подтверждено тестом" : "Самостоятельный выбор"} · ${date(r.assigned_at)} · Смена доступна: ${date(r.next_change_allowed_at)}`}
                />
              ))
            ) : (
              <Empty title="Категория пока не выбрана" />
            )
          }
        </Data>
      </Panel>
    </>
  );
}
function TaskAnswer({
  task,
  testId,
  closed,
}: {
  task: Row;
  testId: string;
  closed: boolean;
}) {
  const [content, setContent] = useState("");
  const [choice, setChoice] = useState("");
  const [language, setLanguage] = useState(
    task.public?.signature?.runtime || "python",
  );
  const [key] = useState(() => crypto.randomUUID());
  const publicData = task.public || {};
  const choices = publicData.options || [];
  const languages = [publicData.signature?.runtime || "python"];
  return (
    <Panel>
      <div className="task-heading">
        <span className="eyebrow">
          Задание {task.position} /{" "}
          {task.kind === "code"
            ? "Код"
            : task.kind === "quiz"
              ? "Выбор ответа"
              : "Развёрнутый ответ"}
        </span>
        <Badge value={task.status} />
      </div>
      <h3>{publicData.title || "Практическая задача"}</h3>
      <Markdown source={task.statement || ""} />
      {publicData.signature && (
        <p className="notice">
          {publicData.signature.runtime} ·{" "}
          {publicData.signature.io === "function"
            ? `Функция ${publicData.signature.entrypoint}`
            : "Программа со стандартным вводом и выводом"}
        </p>
      )}
      {publicData.environment && (
        <details>
          <summary>Окружение и библиотеки</summary>
          <p>{publicData.environment}</p>
        </details>
      )}
      {publicData.examples?.map((e: Row, i: number) => (
        <pre key={i}>
          {typeof e === "string"
            ? e
            : `Ввод: ${e.stdin ?? JSON.stringify(e.input, null, 2)}\nВывод: ${typeof e.expected === "string" ? e.expected : JSON.stringify(e.expected, null, 2)}`}
        </pre>
      ))}
      {task.kind === "quiz" ? (
        choices.map((o: Row) => (
          <label className="choice" key={o.id}>
            <input
              type="radio"
              name={key}
              value={o.id}
              onChange={() => setChoice(o.id)}
              disabled={closed}
            />
            {o.text}
          </label>
        ))
      ) : (
        <>
          {task.kind === "code" && (
            <label className="field">
              Язык
              <select
                value={language}
                onChange={(e) => setLanguage(e.target.value)}
                disabled={closed}
              >
                {languages.map((l: string) => (
                  <option key={l}>{l}</option>
                ))}
              </select>
            </label>
          )}
          {task.kind === "code" ? (
            <Suspense fallback={<Loading />}>
              <Editor
                value={content}
                change={setContent}
                language={language}
                disabled={closed}
              />
            </Suspense>
          ) : (
            <textarea
              aria-label="Ответ на задание"
              rows={9}
              value={content}
              onChange={(e) => setContent(e.target.value)}
              disabled={closed}
            />
          )}
        </>
      )}
      <div className="actions">
        {!closed && (
          <Action
            run={async () => {
              if (task.kind === "quiz" ? !choice : !content.trim())
                throw new Error("Сначала заполните ответ");
              await send(
                `/tests/${testId}/tasks/${task.id}/answer`,
                task.kind === "quiz"
                  ? { choice }
                  : { content, ...(task.kind === "code" ? { language } : {}) },
              );
            }}
          >
            Сохранить ответ
          </Action>
        )}
      </div>
    </Panel>
  );
}
export function TestPage() {
  const { id = "" } = useParams();
  const q = useData(`/tests/${id}`, 3000);
  const [operation, setOperation] = useState("");
  const [showResult, setShowResult] = useState(false);
  const result = useData(
    `/tests/${id}/result`,
    3000,
    showResult || q.data?.result_applied,
  );
  const qc = useQueryClient();
  return (
    <>
      <Heading
        eyebrow="Индивидуальное тестирование"
        title="Подтвердите свой уровень"
        action={<Go to="/candidate/assessment">К истории</Go>}
      >
        Ответы сохраняются после нажатия кнопки. Не закрывайте страницу с
        несохранённым решением
      </Heading>
      <Data query={q}>
        {(r) => {
          const closed = [
            "scored",
            "cancelled",
            "expired",
            "submitted",
          ].includes(r.status);
          return (
            <>
              <Panel>
                <div className="split">
                  <Badge value={r.service_state} />
                  <span>Срок: {date(r.deadline_at)}</span>
                </div>
                {r.service_state === "failed" && (
                  <div className="error" role="alert">
                    <div>
                      <strong>Не удалось сформировать проверенный набор</strong>
                      <p>
                        Категория не подтверждена. Этот технический отказ не
                        расходует кулдаун смены грейда
                      </p>
                      {r.failure && (
                        <details>
                          <summary>Причина отказа</summary>
                          <p>{r.failure}</p>
                        </details>
                      )}
                      <Go to="/candidate/assessment">
                        Вернуться к опросам и попыткам
                      </Go>
                    </div>
                  </div>
                )}
                {!r.tasks?.length && !closed && (
                  <p>
                    Сервис формирует индивидуальный набор. Статус обновляется
                    автоматически
                  </p>
                )}
                <div className="actions">
                  {!closed && (
                    <>
                      <Action
                        disabled={
                          !["ready", "scored"].includes(r.service_state)
                        }
                        confirm="Завершить тест? Проверьте, что все ответы сохранены"
                        run={async () => {
                          const out = await send(`/tests/${id}/finish`);
                          if (out.id) setOperation(out.id);
                          else setShowResult(true);
                          await invalidate(qc);
                        }}
                      >
                        Завершить и проверить
                      </Action>
                      <Action
                        secondary
                        confirm="Отменить эту попытку?"
                        run={async () => {
                          await send(`/tests/${id}/cancel`);
                          await invalidate(qc);
                        }}
                      >
                        Отменить попытку
                      </Action>
                    </>
                  )}
                  {r.result_applied && (
                    <Button secondary onClick={() => setShowResult(true)}>
                      Показать результат
                    </Button>
                  )}
                </div>
              </Panel>
              {r.tasks?.map((task: Row) =>
                task.public && task.statement ? (
                  <TaskAnswer
                    key={task.id}
                    task={task}
                    testId={id}
                    closed={closed}
                  />
                ) : (
                  <Panel key={task.id}>
                    <Badge value={task.status} />
                    <p>
                      Готовим задание {task.position}. Редактор появится после
                      проверки условия
                    </p>
                  </Panel>
                ),
              )}
            </>
          );
        }}
      </Data>
      {operation && (
        <Operation
          id={operation}
          done={() => {
            setShowResult(true);
            invalidate(qc);
          }}
        />
      )}
      {(showResult || q.data?.result_applied) && (
        <Panel>
          <h3>Результат тестирования</h3>
          <Data query={result}>
            {(r) => (
              <>
                <Badge value={r.outcome || r.service_state} />
                <p>Результат применён: {date(r.applied_at)}</p>
                <ResultTree value={r.result} />
              </>
            )}
          </Data>
        </Panel>
      )}
    </>
  );
}
function ResultTree({ value }: { value: any }) {
  if (value == null) return null;
  if (typeof value !== "object") return <span>{String(value)}</span>;
  return (
    <dl className="result-tree">
      {Object.entries(value).map(([key, v]) => (
        <div key={key}>
          <dt>
            {(
              {
                total_score: "Суммарный балл",
                max_score: "Максимальный балл",
                outcome: "Результат",
                feedback: "Обратная связь",
                score: "Баллы",
                tasks: "Задания",
                status: "Состояние",
              } as Row
            )[key] || key}
          </dt>
          <dd>
            <ResultTree value={v} />
          </dd>
        </div>
      ))}
    </dl>
  );
}
export { default as Docs } from "./Docs";
