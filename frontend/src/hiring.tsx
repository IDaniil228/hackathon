import { useEffect, useRef, useState } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  Check,
  FileDown,
  MessageSquare,
  Plus,
  Search,
  SlidersHorizontal,
  X,
  ZoomIn,
  ZoomOut,
  RotateCcw,
} from "lucide-react";
import { request, send, download, ApiError } from "./api/client";
import { action as submitAction } from "./api/actions";
import { invalidate, Operation, useReferences } from "./candidate";
import { useSession } from "./session";
import { Brand, Theme } from "./App";
import { radialDistance } from "./map";
import {
  Action,
  AddLink,
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
  name,
  options,
  salary,
  text,
  useData,
  type Row,
} from "./ui";

export function Company() {
  const q = useData("/me/company");
  const qc = useQueryClient();
  const creating =
    q.error instanceof ApiError && [403, 404, 409].includes(q.error.status);
  return (
    <>
      <Heading eyebrow="Компания" title="Познакомьте кандидатов с командой">
        Название, направление работы и люди за предложением
      </Heading>
      <Panel>
        {q.isPending ? (
          <Loading />
        ) : q.error && !creating ? (
          <ErrorBox error={q.error} />
        ) : (
          <Form
            key={q.data?.id || "new"}
            fields={[
              text("name", "Название компании", true),
              text("full_name", "Имя представителя", true),
              text("city", "Город"),
              text("website", "Сайт компании", false, "url"),
              text("inn", "ИНН"),
              text("description", "О компании и команде", false, "textarea"),
            ]}
            initial={q.data || {}}
            label={creating ? "Создать компанию" : "Сохранить компанию"}
            submit={async (v) => {
              await send(
                creating ? "/companies" : "/me/company",
                v,
                creating ? "POST" : "PUT",
              );
              await invalidate(qc);
            }}
          />
        )}
      </Panel>
    </>
  );
}
export function SkillsPicker({
  values,
  onChange,
}: {
  values: Row[];
  onChange: (values: Row[]) => void;
}) {
  const refs = useReferences();
  const [selected, setSelected] = useState("");
  return (
    <div className="skills-picker">
      <h4>Требуемые навыки</h4>
      {values.map((r, i) => (
        <div className="inline-form" key={r.skill_id}>
          <strong>{refs.skills.find((s) => s.id === r.skill_id)?.name}</strong>
          <label>
            Важность
            <select
              value={r.importance}
              onChange={(e) =>
                onChange(
                  values.map((s, n) =>
                    n === i ? { ...s, importance: e.target.value } : s,
                  ),
                )
              }
            >
              <option value="required">Обязательный</option>
              <option value="preferred">Желательный</option>
            </select>
          </label>
          <label>
            Минимальный уровень
            <select
              value={r.min_level ?? ""}
              onChange={(e) =>
                onChange(
                  values.map((s, n) =>
                    n === i
                      ? {
                          ...s,
                          min_level:
                            e.target.value === ""
                              ? null
                              : Number(e.target.value),
                        }
                      : s,
                  ),
                )
              }
            >
              <option value="">Не задан</option>
              {[0, 1, 2, 3, 4, 5].map((n) => (
                <option key={n}>{n}</option>
              ))}
            </select>
          </label>
          <button
            className="icon-button"
            type="button"
            aria-label="Удалить навык"
            onClick={() => onChange(values.filter((_, n) => n !== i))}
          >
            <X size={17} />
          </button>
        </div>
      ))}
      <div className="inline-form">
        <label>
          Добавить навык
          <select
            value={selected}
            onChange={(e) => setSelected(e.target.value)}
          >
            <option value="">Выберите</option>
            {refs.skills
              .filter((s) => !values.some((v) => v.skill_id === s.id))
              .map((s) => (
                <option value={s.id} key={s.id}>
                  {s.name}
                </option>
              ))}
          </select>
        </label>
        <Button
          secondary
          onClick={() => {
            if (selected) {
              onChange([
                ...values,
                {
                  skill_id: Number(selected),
                  importance: "required",
                  min_level: null,
                },
              ]);
              setSelected("");
            }
          }}
        >
          <Plus size={16} />
          Добавить
        </Button>
      </div>
    </div>
  );
}
export function Needs() {
  const q = useData("/needs");
  return (
    <>
      <Heading
        eyebrow="Поиск для команды"
        title="Поиск сотрудников"
        action={<AddLink to="/employer/needs/new">Новый поиск</AddLink>}
      >
        Откройте сохранённый поиск или опишите нового сотрудника. Семантический
        подбор использует эмбеддинги профилей и требований; результаты можно
        уточнять, сохраняя историю
      </Heading>
      <Panel>
        <Data query={q}>
          {(rows) =>
            rows.length ? (
              rows.map((r: Row) => (
                <ListRow
                  key={r.id}
                  title={r.title}
                  subtitle={`${salary(r)} · ${r.is_active ? "Поиск открыт" : "Поиск закрыт"}`}
                  to={`/employer/needs/${r.id}`}
                >
                  <Go to={`/employer/needs/${r.id}`}>Подобрать кандидатов</Go>
                </ListRow>
              ))
            ) : (
              <Empty title="Кого ищет ваша команда?">
                Создайте первую потребность. Публиковать вакансию для этого
                необязательно
              </Empty>
            )
          }
        </Data>
      </Panel>
    </>
  );
}
export function NeedEditor() {
  const { id } = useParams();
  const refs = useReferences();
  const q = useData(`/needs/${id}`, undefined, !!id);
  const history = useData(`/needs/${id}/runs`, 5000, !!id);
  const vacancies = useData("/me/vacancies");
  const [skills, setSkills] = useState<Row[]>([]);
  const [operation, setOperation] = useState("");
  const navigate = useNavigate();
  const qc = useQueryClient();
  useEffect(() => {
    if (q.data) setSkills(q.data.skills || []);
  }, [q.data]);
  const fields = [
    text("title", "Название потребности", true),
    RefSelect("specialization_id", "Специализация", refs.specs),
    RefSelect("grade_id", "Грейд", refs.grades),
    RefSelect("category_id", "Категория", refs.cats),
    RefSelect("vacancy_id", "Связанная вакансия", vacancies.data || []),
    text("salary_min", "Бюджет от, ₽", false, "number"),
    text("salary_max", "Бюджет до, ₽", false, "number"),
    text(
      "description",
      "Кого ищете и чем занимается команда",
      true,
      "textarea",
    ),
    text("positive_prompt", "Дополнительные пожелания", false, "textarea"),
    text("negative_prompt", "Что не подходит команде", false, "textarea"),
    text("is_active", "Потребность открыта", false, "checkbox"),
  ];
  return (
    <>
      <Heading
        eyebrow="Описание потребности"
        title={
          id
            ? q.data?.title || "Потребность команды"
            : "Кто усилит вашу команду?"
        }
        action={<Go to="/employer/needs">Все потребности</Go>}
      >
        Город и формат поиска берутся из связанной вакансии. Зарплата — за месяц
        до НДФЛ
      </Heading>
      {id && (
        <Panel>
          <div className="split">
            <div>
              <h3>Семантический поиск кандидатов</h3>
              <p>
                Проверим обязательные условия, отберём близкие профили по
                эмбеддингам и откроем подборку с картой кандидатов
              </p>
            </div>
            <Action
              disabled={!q.data?.is_active}
              run={async () => {
                const r = await submitAction(`/needs/${id}/runs`);
                setOperation(r.id);
              }}
            >
              <Search size={17} />
              Начать подбор
            </Action>
          </div>
          {operation && (
            <Operation
              id={operation}
              done={(r) => navigate(`/employer/runs/${r.remote_id}`)}
            />
          )}
        </Panel>
      )}
      <Panel>
        {id && q.isPending ? (
          <Loading />
        ) : q.error ? (
          <ErrorBox error={q.error} />
        ) : (
          <Form
            key={id || "new"}
            fields={fields}
            initial={q.data || { is_active: true }}
            label={id ? "Сохранить потребность" : "Создать потребность"}
            submit={async (v) => {
              const result = await send(
                id ? `/needs/${id}` : "/needs",
                { ...v, skills },
                id ? "PUT" : "POST",
              );
              await invalidate(qc);
              if (!id) navigate(`/employer/needs/${result.id}`);
            }}
          >
            <SkillsPicker values={skills} onChange={setSkills} />
          </Form>
        )}
      </Panel>
      {id && (
        <>
          <Panel>
            <h3>История поиска</h3>
            <Data query={history}>
              {(rows) =>
                rows.length ? (
                  rows.map((r: Row) => (
                    <ListRow
                      key={r.id}
                      title={`Подбор от ${date(r.created_at)}`}
                      subtitle={
                        r.parent_run_id
                          ? "Уточнённая подборка"
                          : "Первоначальная подборка"
                      }
                      status={r.status}
                      to={`/employer/runs/${r.id}`}
                    />
                  ))
                ) : (
                  <Empty title="Поиск ещё не запускали" />
                )
              }
            </Data>
          </Panel>
        </>
      )}
    </>
  );
}
function CandidateCard({
  candidate,
  to,
  rank,
  score,
  selected,
  click,
  factors,
}: {
  candidate: Row;
  to?: string;
  rank?: number;
  score?: number;
  selected?: boolean;
  click?: () => void;
  factors?: Row;
}) {
  return (
    <article className={`candidate-card ${selected ? "selected" : ""}`}>
      <div className="candidate-top">
        <span className="candidate-avatar">
          {candidate.first_name?.slice(0, 1) || "?"}
        </span>
        <div>
          <h3>
            {to ? (
              <Link to={to}>{name(candidate)}</Link>
            ) : click ? (
              <button className="text-button" onClick={click}>
                {name(candidate)}
              </button>
            ) : (
              <span>{name(candidate)}</span>
            )}
          </h3>
          <p>{candidate.category?.name || "Категория не выбрана"}</p>
        </div>
        {rank && <span className="rank">#{rank}</span>}
      </div>
      <div className="candidate-meta">
        {candidate.demo && <span>Синтетический профиль</span>}
        {candidate.city && <span>{candidate.city}</span>}
        {candidate.experience_months != null && (
          <span>
            {Math.floor(candidate.experience_months / 12)} лет{" "}
            {candidate.experience_months % 12} мес.
          </span>
        )}
        {candidate.grade_confirmed && (
          <span className="verified">
            <Check size={13} />
            Грейд подтверждён
          </span>
        )}
      </div>
      {candidate.about && <p className="candidate-about">{candidate.about}</p>}
      <div className="tags">
        {candidate.skills?.slice(0, 6).map((s: Row) => (
          <span
            key={s.code}
            className={s.confirmed_level != null ? "confirmed" : ""}
          >
            {s.confirmed_level != null && <Check size={11} />}
            {s.name}
          </span>
        ))}
      </div>
      {factors && (
        <details className="factor-details">
          <summary>Почему в подборке</summary>
          {Object.entries(factors).map(([key, factor]) => (
            <div key={key} className="factor-row">
              <span>
                {(
                  {
                    stack: "Стек",
                    grade: "Грейд",
                    salary: "Зарплата",
                    percentile: "Сила профиля",
                    fsp: "Достижения ФСП",
                    freshness: "Актуальность",
                  } as Row
                )[key] || key}
              </span>
              {(factor as Row).applicable ? (
                <>
                  <meter
                    min={0}
                    max={1}
                    value={(factor as Row).value}
                    aria-label={key}
                  />
                  <span>{Math.round((factor as Row).value * 100)}/100</span>
                </>
              ) : (
                <span>Не применим</span>
              )}
            </div>
          ))}
        </details>
      )}
      <div className="candidate-bottom">
        <span>
          {candidate.preferences
            ? salary(candidate.preferences)
            : "Ожидания скрыты"}
        </span>
        {score != null && (
          <span className="score">
            Соответствие {Math.round(score * 100)}/100
          </span>
        )}
        {to ? (
          <Link to={to} aria-label={`Открыть профиль ${name(candidate)}`}>
            <ArrowRight size={18} />
          </Link>
        ) : click ? (
          <button
            className="icon-button"
            aria-label={`Выбрать ${name(candidate)}`}
            onClick={click}
          >
            <ArrowRight size={18} />
          </button>
        ) : null}
        {click && to && (
          <button
            className="icon-button"
            aria-label={`Показать на карте ${name(candidate)}`}
            onClick={click}
          >
            <Search size={15} />
          </button>
        )}
      </div>
    </article>
  );
}
export function Candidates() {
  const refs = useReferences();
  const [filters, setFilters] = useState<Row>({});
  const [offset, setOffset] = useState(0);
  const params = new URLSearchParams({
    limit: "20",
    offset: String(offset),
    ...Object.fromEntries(
      Object.entries(filters)
        .filter(([, v]) => v != null && v !== "")
        .map(([k, v]) => [k, String(v)]),
    ),
  });
  const q = useData(`/candidates?${params}`);
  return (
    <>
      <Heading
        eyebrow="Банк профилей"
        title="Люди, с которыми стоит познакомиться"
      >
        Каталог доступных профилей. Для смыслового подбора создайте потребность
        команды
      </Heading>
      <Panel>
        <Form
          fields={[
            RefSelect("specialization_id", "Специализация", refs.specs),
            RefSelect("grade_id", "Грейд", refs.grades),
            RefSelect("category_id", "Категория", refs.cats),
            options("grade_confirmed", "Подтверждение", [
              ["true", "Подтверждён"],
              ["false", "Пока не подтверждён"],
            ]),
          ]}
          label="Применить фильтры"
          submit={async (v) => {
            setFilters(v);
            setOffset(0);
          }}
        />
      </Panel>
      <Data query={q}>
        {(r) => (
          <>
            <div className="results-toolbar">
              <span>{r.total} доступных профилей</span>
              <span>Сначала сильные профили</span>
            </div>
            <div className="candidate-grid">
              {r.results.map((c: Row) => (
                <CandidateCard
                  key={c.user_id}
                  candidate={c}
                  to={`/employer/candidates/${c.user_id}`}
                />
              ))}
            </div>
            {!r.results.length && (
              <Panel>
                <Empty title="Совпадений пока нет">
                  Попробуйте ослабить фильтры
                </Empty>
              </Panel>
            )}
            <div className="pagination">
              <Button
                secondary
                disabled={!offset}
                onClick={() => setOffset(Math.max(0, offset - 20))}
              >
                Назад
              </Button>
              <span>
                {offset + 1}–{Math.min(offset + 20, r.total)} из {r.total}
              </span>
              <Button
                secondary
                disabled={offset + 20 >= r.total}
                onClick={() => setOffset(offset + 20)}
              >
                Далее
              </Button>
            </div>
          </>
        )}
      </Data>
    </>
  );
}
export function CandidatePage() {
  const { id = "" } = useParams();
  const q = useData(`/candidates/${id}`);
  const [action, setAction] = useState("");
  const qc = useQueryClient();
  const navigate = useNavigate();
  return (
    <>
      <Heading
        eyebrow="Профиль кандидата"
        title={q.data ? name(q.data) : "Профиль"}
        action={
          <Action
            secondary
            run={() => download(`/candidates/${id}/profile.pdf`)}
          >
            <FileDown size={17} />
            Скачать PDF
          </Action>
        }
      >
        Доступные сведения учитывают текущие настройки кандидата
      </Heading>
      <Data query={q}>
        {(r) => (
          <>
            <CandidateCard candidate={r} />
            <Panel>
              <h3>Навыки и подтверждения</h3>
              {r.skills?.map((s: Row) => (
                <ListRow
                  key={s.code}
                  title={s.name}
                  subtitle={`Заявлено ${s.self_level}/5 · ${s.confirmed_level != null ? `Подтверждено ${s.confirmed_level}/5` : "Пока не проверено"}`}
                />
              ))}
              {r.fsp_achievements?.length ? (
                <>
                  <h3>Достижения ФСП</h3>
                  {r.fsp_achievements.map((a: Row) => (
                    <ListRow
                      key={a.id}
                      title={a.event_title}
                      subtitle={`Место ${a.place || "—"} из ${a.participants || "—"}${a.demo ? " · Демонстрационные данные" : ""}`}
                    />
                  ))}
                </>
              ) : (
                <p className="muted">Доступной истории ФСП нет</p>
              )}
              {r.contacts && (
                <>
                  <h3>Контакты</h3>
                  {Object.entries(r.contacts).map(([k, v]) => (
                    <p key={k}>
                      {k}: {String(v || "—")}
                    </p>
                  ))}
                </>
              )}
              {r.links?.map((l: Row) => (
                <p key={l.id}>
                  <a href={l.url} target="_blank" rel="noreferrer">
                    {l.label || l.kind} ↗
                  </a>
                </p>
              ))}
            </Panel>
            <Panel>
              <h3>Сделайте первый шаг</h3>
              <p>
                Предложите конкретные условия. Кандидат увидит зарплату до
                начала общения
              </p>
              <div className="actions">
                <Button
                  onClick={() => setAction(action === "offer" ? "" : "offer")}
                >
                  Направить предложение
                  <ArrowRight size={16} />
                </Button>
                <Button
                  secondary
                  onClick={() => setAction(action === "task" ? "" : "task")}
                >
                  Короткое задание
                </Button>
              </div>
              {action === "offer" && (
                <OfferForm
                  submit={async (v) => {
                    const offer = await submitAction("/invitations", {
                      ...v,
                      candidate_id: id,
                    });
                    const chat = await send(
                      `/invitations/${offer.id}/conversation`,
                    );
                    await invalidate(qc);
                    navigate(`/employer/conversations/${chat.id}`);
                  }}
                />
              )}
              {action === "task" && (
                <Form
                  fields={[
                    text("title", "Название задания", true),
                    text("statement", "Условие", true, "textarea"),
                    text("due_at", "Срок ответа", false, "datetime-local"),
                    {
                      ...text("max_score", "Максимальный балл", true, "number"),
                      min: 1,
                      max: 100,
                    },
                  ]}
                  initial={{ max_score: 10 }}
                  label="Выдать задание"
                  submit={async (v) => {
                    await send("/assignments", { ...v, candidate_id: id });
                    await invalidate(qc);
                    navigate("/employer/assignments");
                  }}
                />
              )}
            </Panel>
          </>
        )}
      </Data>
    </>
  );
}
function OfferForm({ submit }: { submit: (v: Row) => Promise<unknown> }) {
  const needs = useData("/needs");
  const vacancies = useData("/me/vacancies");
  return (
    <Form
      fields={[
        RefSelect("need_id", "Связать с потребностью", needs.data || []),
        RefSelect("vacancy_id", "Связать с вакансией", vacancies.data || []),
        text("message", "Предложение и условия работы", true, "textarea"),
        text("salary_min", "Зарплата от, ₽", true, "number"),
        text("salary_max", "Зарплата до, ₽", true, "number"),
        options("contact_method", "Как связаться", [
          ["platform", "Чат на платформе"],
          ["email", "Почта"],
          ["phone", "Телефон"],
          ["telegram", "Telegram"],
        ]),
        text(
          "contact_value",
          "Контакт работодателя — только для внешней связи",
        ),
      ]}
      initial={{ contact_method: "platform" }}
      label="Отправить предложение"
      submit={submit}
    >
      <p className="muted">
        RUB за месяц до вычета НДФЛ. Контакты кандидата не раскроются от
        простого ответа в чате
      </p>
    </Form>
  );
}
export function RunPage() {
  const { id = "" } = useParams();
  const run = useData(`/runs/${id}`, 2000);
  const [filters, setFilters] = useState<Row>({});
  const refs = useReferences();
  const params = new URLSearchParams();
  Object.entries(filters)
    .filter(([, value]) => value != null)
    .forEach(([key, value]) => {
      if (Array.isArray(value))
        value
          .filter(Boolean)
          .forEach((item) => params.append(key, String(item)));
      else params.set(key, String(value));
    });
  const results = useData(
    `/runs/${id}/results?${params}`,
    undefined,
    run.data?.status === "done",
  );
  const map = useData(`/runs/${id}/map`, 5000, !!run.data);
  const [selected, setSelected] = useState("");
  const [mode, setMode] = useState("cloud");
  const [operation, setOperation] = useState("");
  const navigate = useNavigate();
  const [filterOpen, setFilterOpen] = useState(false);
  const [resultPage, setResultPage] = useState(0);
  useEffect(() => {
    setSelected("");
    setResultPage(0);
  }, [id, filters]);
  return (
    <>
      <Heading
        eyebrow="Смысловой поиск"
        title="Найдите точку сцепления"
        action={
          <Button secondary onClick={() => setFilterOpen(!filterOpen)}>
            <SlidersHorizontal size={17} />
            Фильтры
          </Button>
        }
      >
        Карта помогает исследовать пул. Итоговый порядок учитывает подтверждения
        и факторы, а не только эмбеддинги
      </Heading>
      <Data query={run}>
        {(r) => (
          <div className="results-toolbar">
            <Badge value={r.status} />
            <span>Подбор от {date(r.created_at)}</span>
            {r.pool_size != null && (
              <span>Рассмотрено {r.pool_size} профилей</span>
            )}
          </div>
        )}
      </Data>
      {filterOpen && (
        <Panel>
          <Form
            fields={[
              text("city", "Город"),
              text("salary_max", "Бюджет до, ₽", false, "number"),
              options("work_format", "Формат", formatOptions),
              options("relocation", "Готовность к переезду", [
                ["true", "Готов"],
                ["false", "Не готов"],
              ]),
              {
                name: "skills",
                label: "Все выбранные навыки",
                type: "multi-select",
                options: refs.skills.map((s) => ({
                  value: s.code,
                  label: s.name,
                })),
              },
              options("grade_confirmed", "Грейд", [
                ["true", "Подтверждён"],
                ["false", "Не подтверждён"],
              ]),
              {
                ...text(
                  "min_score",
                  "Минимальная оценка, 0–1",
                  false,
                  "number",
                ),
                max: 1,
              },
            ]}
            submit={async (v) => setFilters(v)}
            label="Применить"
          />
        </Panel>
      )}
      <Panel className="map-panel">
        {map.data?.phase === "preview" && (
          <p className="notice">
            Предварительная карта · оценка короткого списка ещё идёт
          </p>
        )}
        <div className="split">
          <div>
            <p className="eyebrow">Карта кандидатов</p>
            <h3>
              {mode === "cloud" ? "Семантическое облако" : "Близость к запросу"}
            </h3>
          </div>
          <div className="segmented">
            <button
              aria-pressed={mode === "cloud"}
              onClick={() => setMode("cloud")}
            >
              Облако
            </button>
            <button
              aria-pressed={mode === "radial"}
              onClick={() => setMode("radial")}
            >
              Радиальная
            </button>
          </div>
        </div>
        {map.isPending ? (
          <Loading />
        ) : map.error ? (
          <div className="map-unavailable">
            <p>Геометрия пока недоступна</p>
            <small>{map.error.message}</small>
          </div>
        ) : map.data?.points?.length ? (
          <CandidateMap
            data={map.data}
            mode={mode}
            selected={selected}
            select={setSelected}
          />
        ) : (
          <Empty
            title={
              run.data?.status === "done"
                ? "Нет доступных векторов"
                : "Формируем пространство поиска"
            }
          >
            Точки появятся после получения настоящих эмбеддингов
          </Empty>
        )}
        <p className="map-caption">
          {mode === "cloud"
            ? "Проекция приближённая: расстояние на экране не равно расстоянию исходных векторов"
            : "Радиус точно отражает косинусную близость к запросу; расстояния между кандидатами приближённые"}
          . Кластеры — группы похожих профилей, не категории квалификации
        </p>
      </Panel>
      {selected &&
        map.data?.points?.find((p: Row) => p.candidate_id === selected)
          ?.candidate && (
          <Panel>
            <div className="split">
              <h3>Выбранный профиль</h3>
              <Button secondary onClick={() => setSelected("")}>
                Снять выбор
              </Button>
            </div>
            <CandidateCard
              candidate={
                map.data.points.find((p: Row) => p.candidate_id === selected)
                  .candidate
              }
              to={`/employer/candidates/${selected}`}
            />
          </Panel>
        )}
      {run.data?.status === "done" ? (
        <Data query={results}>
          {(r) => (
            <>
              <div className="results-toolbar">
                <strong>
                  Рекомендации · {r.matched} из {r.total}
                </strong>
                <span>Подтверждённый грейд — выше заявленного</span>
              </div>
              <div className="candidate-grid">
                {r.results
                  .slice(resultPage * 12, (resultPage + 1) * 12)
                  .map((row: Row) => (
                    <CandidateCard
                      key={row.candidate_id}
                      candidate={row.candidate}
                      rank={row.rank}
                      score={row.final_score}
                      factors={row.factors}
                      click={() => setSelected(row.candidate_id)}
                      selected={selected === row.candidate_id}
                      to={`/employer/candidates/${row.candidate_id}`}
                    />
                  ))}
              </div>
              {r.results.length > 12 && (
                <div className="pagination">
                  <Button
                    secondary
                    disabled={resultPage === 0}
                    onClick={() => setResultPage(resultPage - 1)}
                  >
                    Назад
                  </Button>
                  <span>
                    {resultPage + 1} / {Math.ceil(r.results.length / 12)}
                  </span>
                  <Button
                    secondary
                    disabled={(resultPage + 1) * 12 >= r.results.length}
                    onClick={() => setResultPage(resultPage + 1)}
                  >
                    Далее
                  </Button>
                </div>
              )}
              {!r.results.length && (
                <Panel>
                  <Empty title="Подборка пуста">
                    Измените фильтры или уточните потребность
                  </Empty>
                </Panel>
              )}
            </>
          )}
        </Data>
      ) : run.data?.status === "failed" ? (
        <Panel>
          <Empty title="Подбор не завершился">
            Проверьте доступность сервисов и запустите новый поиск из
            потребности
          </Empty>
        </Panel>
      ) : (
        <Panel>
          <Loading />
          <p>
            Расчёт идёт на сервере. Не нужно оставлять эту страницу открытой
          </p>
        </Panel>
      )}
      <Panel>
        <h3>Уточните запрос</h3>
        <p>
          Новая подборка сохранит связь с предыдущей. Старый результат останется
          в истории
        </p>
        <Form
          fields={[text("message", "Что изменить в поиске?", true, "textarea")]}
          label="Уточнить подбор"
          submit={async (v) => {
            const op = await submitAction(`/runs/${id}/refine`, v);
            setOperation(op.id);
          }}
        />
        {operation && (
          <Operation
            id={operation}
            done={(r) => {
              setOperation("");
              navigate(`/employer/runs/${r.remote_id}`);
            }}
          />
        )}
      </Panel>
    </>
  );
}
function CandidateMap({
  data,
  mode,
  selected,
  select,
}: {
  data: Row;
  mode: string;
  selected: string;
  select: (id: string) => void;
}) {
  const [zoom, setZoom] = useState(mode === "radial" ? 3 : 1);
  useEffect(() => setZoom(mode === "radial" ? 3 : 1), [mode]);
  const points = data.points;
  const [cluster, setCluster] = useState<number | null>(null);
  const clusters = [
    ...new Set<number>(
      points.map((p: Row) => p.cluster).filter((n: number) => n >= 0),
    ),
  ];
  const maxX = Math.max(
    0.01,
    Math.abs(data.query?.x || 0),
    ...points.map((p: Row) => Math.abs(p.x)),
  );
  const maxY = Math.max(
    0.01,
    Math.abs(data.query?.y || 0),
    ...points.map((p: Row) => Math.abs(p.y)),
  );
  const scale = Math.min(290 / maxX, 180 / maxY);
  const projected = points.map((p: Row) => {
    const angle = Math.atan2(
      p.y - (data.query?.y || 0),
      p.x - (data.query?.x || 0),
    );
    const radius = radialDistance(p.cosine);
    return {
      ...p,
      px: mode === "radial" ? Math.cos(angle) * radius * 210 : p.x * scale,
      py: mode === "radial" ? Math.sin(angle) * radius * 210 : p.y * scale,
    };
  });
  return (
    <>
      <div className="map-canvas">
        <svg
          viewBox="0 0 800 470"
          role="img"
          aria-label={
            mode === "cloud"
              ? "Семантическая карта кандидатов"
              : "Радиальная карта кандидатов"
          }
        >
          <defs>
            <pattern
              id="grid"
              width="30"
              height="30"
              patternUnits="userSpaceOnUse"
            >
              <circle cx="1" cy="1" r="1" className="grid-dot" />
            </pattern>
          </defs>
          <rect width="800" height="470" fill="url(#grid)" />
          <g transform={`translate(400 235) scale(${zoom})`}>
            {mode === "radial" &&
              [0.25, 0.5, 0.75, 1].map((r) => (
                <g key={r}>
                  <circle r={r * 210} className="radial-ring" />
                  <text
                    x={5 / zoom}
                    y={-r * 210 + 14 / zoom}
                    style={{ fontSize: 10 / zoom }}
                    className="map-label"
                  >
                    {(1 - 2 * r).toFixed(1)}
                  </text>
                </g>
              ))}
            {projected.map((p: Row) => (
              <circle
                key={p.candidate_id}
                className={`map-dot cluster-${(((p.cluster || 0) % 4) + 4) % 4} ${p.candidate_id === selected ? "active" : ""} ${p.rank ? "ranked" : ""}`}
                cx={p.px}
                cy={p.py}
                r={(p.candidate_id === selected ? 8 : p.rank ? 5 : 3.5) / zoom}
                vectorEffect="non-scaling-stroke"
                opacity={cluster == null || p.cluster === cluster ? 1 : 0.12}
                onClick={() => select(p.candidate_id)}
              >
                <title>
                  {name(p.candidate || {})} · близость {p.cosine.toFixed(3)} ·{" "}
                  {p.rank ? `место ${p.rank}` : "Вне короткого списка"}
                  {p.exploration &&
                    ` · ${p.exploration === "fresh" ? "Новый профиль" : "Случайная разведка"}`}
                </title>
              </circle>
            ))}
            <circle
              cx={mode === "radial" ? 0 : (data.query?.x || 0) * scale}
              cy={mode === "radial" ? 0 : (data.query?.y || 0) * scale}
              r={9 / zoom}
              vectorEffect="non-scaling-stroke"
              className="query-dot"
            />
            <text
              x={mode === "radial" ? 15 : (data.query?.x || 0) * scale + 15}
              y={mode === "radial" ? 4 : (data.query?.y || 0) * scale + 4}
              className="map-label"
              style={{ fontSize: 10 / zoom }}
            >
              Ваш запрос
            </text>
          </g>
        </svg>
        <div className="map-controls">
          <button
            aria-label="Приблизить"
            onClick={() => setZoom(Math.min(8, zoom + 0.5))}
          >
            <ZoomIn size={17} />
          </button>
          <button
            aria-label="Отдалить"
            onClick={() => setZoom(Math.max(0.5, zoom - 0.25))}
          >
            <ZoomOut size={17} />
          </button>
          <button
            aria-label="Сбросить масштаб"
            onClick={() => setZoom(mode === "radial" ? 3 : 1)}
          >
            <RotateCcw size={16} />
          </button>
        </div>
      </div>
      <div className="map-legend">
        <button
          className={cluster == null ? "active" : ""}
          onClick={() => setCluster(null)}
        >
          Все профили · {points.length}
        </button>
        {clusters.map((c) => (
          <button
            className={cluster === c ? "active" : ""}
            key={c}
            onClick={() => setCluster(cluster === c ? null : c)}
          >
            <span className={`legend-dot cluster-${c % 4}`} />
            Группа {c + 1} · {points.filter((p: Row) => p.cluster === c).length}
          </button>
        ))}
      </div>
      <details className="map-accessible-list">
        <summary>Профили на карте — доступный список</summary>
        {points
          .filter((p: Row) => cluster == null || p.cluster === cluster)
          .map((p: Row) => (
            <button
              key={p.candidate_id}
              className="map-list-item"
              aria-pressed={selected === p.candidate_id}
              onClick={() => select(p.candidate_id)}
            >
              {name(p.candidate || {})}
              <span>
                Близость {p.cosine.toFixed(3)}{" "}
                {p.rank ? `· #${p.rank}` : "· вне выдачи"}
              </span>
            </button>
          ))}
      </details>
    </>
  );
}
export function Invitations({ role }: { role: "candidate" | "employer" }) {
  const q = useData(`/invitations?cabinet=${role}`, 5000);
  const qc = useQueryClient();
  const navigate = useNavigate();
  return (
    <>
      <Heading
        eyebrow="Первый контакт"
        title={
          role === "candidate" ? "Предложения для вас" : "Ваши предложения"
        }
      >
        Условия и зарплата видны до начала общения. Ответ в чате не означает
        принятие
      </Heading>
      <Data query={q}>
        {(rows) =>
          rows.length ? (
            <div className="offer-grid">
              {rows.map((r: Row) => (
                <Panel key={r.id}>
                  <div className="split">
                    <span className="eyebrow">
                      {r.company_name || "Предложение команды"}
                    </span>
                    <Badge value={r.status} />
                  </div>
                  <h2 className="offer-salary">{salary(r)}</h2>
                  <small>В месяц до вычета НДФЛ</small>
                  <p className="statement">{r.message}</p>
                  <p className="muted">
                    {date(r.created_at)} ·{" "}
                    {r.contact_method === "platform"
                      ? "Общение на платформе"
                      : `${r.contact_method}: ${r.contact_value}`}
                  </p>
                  <div className="actions">
                    <Action
                      secondary
                      run={async () => {
                        const chat = await send(
                          `/invitations/${r.id}/conversation`,
                        );
                        navigate(`/${role}/conversations/${chat.id}`);
                      }}
                    >
                      <MessageSquare size={16} />
                      Открыть диалог
                    </Action>
                    {["sent", "viewed"].includes(r.status) &&
                      (role === "candidate" ? (
                        <>
                          <Action
                            run={async () => {
                              await send(`/invitations/${r.id}/accept`);
                              await qc.resetQueries();
                            }}
                          >
                            Принять
                          </Action>
                          <Action
                            secondary
                            confirm="Отклонить предложение?"
                            run={async () => {
                              await send(`/invitations/${r.id}/decline`);
                              await invalidate(qc);
                            }}
                          >
                            Отклонить
                          </Action>
                          {r.status === "sent" && (
                            <Action
                              secondary
                              run={async () => {
                                await send(`/invitations/${r.id}/view`);
                                await invalidate(qc);
                              }}
                            >
                              Отметить просмотр
                            </Action>
                          )}
                        </>
                      ) : (
                        <Action
                          secondary
                          confirm="Отозвать предложение?"
                          run={async () => {
                            await send(`/invitations/${r.id}/withdraw`);
                            await invalidate(qc);
                          }}
                        >
                          Отозвать
                        </Action>
                      ))}
                  </div>
                </Panel>
              ))}
            </div>
          ) : (
            <Panel>
              <Empty
                title={
                  role === "candidate"
                    ? "Ваше первое предложение ещё впереди"
                    : "Пока нет отправленных предложений"
                }
              >
                {role === "candidate"
                  ? "Дополните профиль и подтвердите навыки — работодателю будет проще вас найти"
                  : "Выберите кандидата в подборке и предложите ему конкретные условия"}
              </Empty>
            </Panel>
          )
        }
      </Data>
    </>
  );
}
export function Conversations({ role }: { role: string }) {
  const q = useData("/conversations", 5000);
  return (
    <>
      <Heading eyebrow="Прямой разговор" title="Сообщения">
        Каждый диалог связан с конкретным предложением
      </Heading>
      <Panel>
        <Data query={q}>
          {(r) =>
            r.results.length ? (
              r.results.map((c: Row) => (
                <ListRow
                  key={c.id}
                  title={
                    c.offer.company_name || `Предложение ${salary(c.offer)}`
                  }
                  subtitle={`${c.offer.message.slice(0, 100)} · ${date(c.updated_at)}`}
                  to={`/${role}/conversations/${c.id}`}
                  status={c.offer.status}
                >
                  {c.unread_count > 0 && (
                    <span className="unread">{c.unread_count}</span>
                  )}
                </ListRow>
              ))
            ) : (
              <Empty title="Пока нет диалогов">
                Общение открывается после предложения работодателя
              </Empty>
            )
          }
        </Data>
      </Panel>
    </>
  );
}
export function Conversation({ role }: { role: string }) {
  const { id = "" } = useParams();
  const q = useData(`/conversations/${id}`, 3000);
  const [messages, setMessages] = useState<Row[]>([]);
  const cursor = useRef(0);
  const [content, setContent] = useState("");
  const pending = useRef<{ content: string; key: string } | null>(null);
  const [error, setError] = useState<unknown>();
  const qc = useQueryClient();
  const session = useSession();
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let active = true;
    let busy = false;
    cursor.current = 0;
    setMessages([]);
    const poll = async () => {
      if (busy || document.hidden) return;
      busy = true;
      try {
        let more = true;
        while (more && active) {
          const r = await request(
            `/conversations/${id}/messages?after_id=${cursor.current}&limit=100`,
          );
          if (!active) return;
          setMessages((old) => [
            ...old,
            ...r.results.filter((n: Row) => !old.some((o) => o.id === n.id)),
          ]);
          cursor.current = r.next_cursor;
          more = r.has_more;
        }
        if (cursor.current && active)
          await send(`/conversations/${id}/read`, {
            up_to_message_id: cursor.current,
          });
      } catch (e) {
        if (active) setError(e);
      } finally {
        busy = false;
      }
    };
    poll();
    const timer = setInterval(poll, 2500);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [id]);
  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "instant", block: "nearest" });
  }, [messages.length]);
  return (
    <>
      <Heading
        eyebrow="Переписка"
        title="Договоритесь о следующем шаге"
        action={<Go to={`/${role}/conversations`}>Все диалоги</Go>}
      />
      <Data query={q}>
        {(c) => (
          <div className="chat-layout">
            <Panel className="chat">
              <div className="chat-header">
                <Badge value={c.offer.status} />
                <span>{c.can_send ? "Диалог открыт" : "Отправка закрыта"}</span>
              </div>
              <div className="message-history">
                {messages.map((m) => (
                  <div
                    key={m.id}
                    className={`message ${m.sender_user_id === session.user?.id ? "mine" : ""} ${m.kind === "offer" ? "offer-message" : ""}`}
                  >
                    {m.kind === "offer" && (
                      <>
                        <span className="eyebrow">
                          Предложение работодателя
                        </span>
                        <h3>{salary(c.offer)}</h3>
                        <small>В месяц до вычета НДФЛ</small>
                      </>
                    )}
                    <p>{m.content}</p>
                    <small>
                      {date(m.created_at)}
                      {m.sender_user_id === session.user?.id &&
                      m.id <= c.peer_read_id
                        ? " · Прочитано"
                        : ""}
                    </small>
                  </div>
                ))}
                <div ref={end} />
              </div>
              <ErrorBox error={error} />
              <div className="composer">
                <textarea
                  aria-label="Сообщение"
                  placeholder={
                    c.can_send
                      ? "Напишите сообщение…"
                      : "Диалог закрыт для новых сообщений"
                  }
                  value={content}
                  onChange={(e) => setContent(e.target.value)}
                  disabled={!c.can_send}
                  maxLength={5000}
                />
                <Action
                  disabled={!c.can_send || !content.trim()}
                  run={async () => {
                    if (!c.can_send || !content.trim())
                      throw new Error(
                        "Отправка недоступна или сообщение пустое",
                      );
                    if (!pending.current)
                      pending.current = { content, key: crypto.randomUUID() };
                    if (pending.current.content !== content)
                      throw new Error(
                        "Сначала повторите прежнее сообщение: его результат не подтверждён",
                      );
                    const m = await send(`/conversations/${id}/messages`, {
                      content: pending.current.content,
                      client_message_id: pending.current.key,
                    });
                    setMessages((old) =>
                      old.some((x) => x.id === m.id) ? old : [...old, m],
                    );
                    pending.current = null;
                    setContent("");
                    await invalidate(qc);
                  }}
                >
                  Отправить
                  <ArrowRight size={16} />
                </Action>
              </div>
            </Panel>
            <Panel className="chat-context">
              <p className="eyebrow">Основа разговора</p>
              <h3>{c.offer.company_name || "Предложение команды"}</h3>
              <h2>{salary(c.offer)}</h2>
              <p>В месяц до вычета НДФЛ</p>
              <p className="statement">{c.offer.message}</p>
              <p className="muted">
                Ответ здесь не принимает предложение и не раскрывает контакты
              </p>
              <Action
                secondary
                confirm="Закрыть диалог для новых сообщений? История останется доступной"
                run={async () => {
                  await send(`/conversations/${id}/close`);
                  await invalidate(qc);
                }}
              >
                Закрыть диалог
              </Action>
            </Panel>
          </div>
        )}
      </Data>
    </>
  );
}
export function Vacancies({ publicView = false }: { publicView?: boolean }) {
  const q = useData(publicView ? "/vacancies" : "/me/vacancies");
  const refs = useReferences();
  const content = (
    <>
      <Heading
        eyebrow="Возможности команды"
        title={publicView ? "Открытые вакансии" : "Вакансии вашей компании"}
        action={
          !publicView && (
            <AddLink to="/employer/vacancies/new">Новая вакансия</AddLink>
          )
        }
      >
        Самостоятельный отклик дополняет основную механику приглашений
      </Heading>
      <Panel>
        <Data query={q}>
          {(rows) =>
            rows.length ? (
              rows.map((r: Row) => (
                <ListRow
                  key={r.id}
                  title={r.title}
                  subtitle={`${salary(r)} · ${refs.cats.find((c) => c.id === r.category_id)?.name || ""}${r.city ? " · " + r.city : ""}`}
                  to={
                    publicView
                      ? `/vacancies/${r.id}`
                      : `/employer/vacancies/${r.id}`
                  }
                  status={r.status}
                />
              ))
            ) : (
              <Empty title="Вакансий пока нет" />
            )
          }
        </Data>
      </Panel>
    </>
  );
  return publicView ? (
    <div className="public-content">
      <nav className="public-nav">
        <Brand />
        <div className="nav-links">
          <Theme />
          <Go to="/">Главная</Go>
        </div>
      </nav>
      <main>{content}</main>
    </div>
  ) : (
    content
  );
}
export function VacancyEditor() {
  const { id } = useParams();
  const q = useData(`/vacancies/${id}`, undefined, !!id);
  const refs = useReferences();
  const [skills, setSkills] = useState<Row[]>([]);
  const qc = useQueryClient();
  const navigate = useNavigate();
  useEffect(() => {
    if (q.data) setSkills(q.data.skills || []);
  }, [q.data]);
  return (
    <>
      <Heading
        eyebrow="Вакансия"
        title={
          id
            ? q.data?.title || "Редактирование вакансии"
            : "Пригласите в команду"
        }
      />
      <Panel>
        {id && q.isPending ? (
          <Loading />
        ) : q.error ? (
          <ErrorBox error={q.error} />
        ) : (
          <Form
            key={id || "new"}
            fields={[
              text("title", "Название вакансии", true),
              RefSelect("category_id", "Категория", refs.cats, true),
              text("salary_min", "Зарплата от, ₽", true, "number"),
              text("salary_max", "Зарплата до, ₽", true, "number"),
              options("work_format", "Формат работы", formatOptions),
              options("employment_type", "Занятость", [
                ["full_time", "Полная"],
                ["part_time", "Частичная"],
                ["project", "Проектная"],
                ["internship", "Стажировка"],
              ]),
              text("city", "Город"),
              text("description", "Описание и условия", true, "textarea"),
            ]}
            initial={q.data || {}}
            submit={async (v) => {
              const row = await send(
                id ? `/vacancies/${id}` : "/vacancies",
                { ...v, skills },
                id ? "PUT" : "POST",
              );
              await invalidate(qc);
              if (!id) navigate(`/employer/vacancies/${row.id}`);
            }}
          >
            <SkillsPicker values={skills} onChange={setSkills} />
            <small>RUB за месяц до вычета НДФЛ</small>
          </Form>
        )}
      </Panel>
      {id && (
        <Panel>
          <h3>Публикация</h3>
          <Badge value={q.data?.status || "draft"} />
          <div className="actions">
            {[
              ["published", "Опубликовать"],
              ["paused", "Приостановить"],
              ["archived", "В архив"],
            ].map(([status, label]) => (
              <Action
                key={status}
                secondary
                confirm={
                  status === "archived"
                    ? "Архивировать вакансию? Возврат из архива недоступен"
                    : undefined
                }
                run={async () => {
                  await send(`/vacancies/${id}/status`, { status });
                  await invalidate(qc);
                }}
              >
                {label}
              </Action>
            ))}
          </div>
        </Panel>
      )}
    </>
  );
}
export function VacancyPage() {
  const { id } = useParams();
  const session = useSession();
  const q = useData(`/vacancies/${id}`, undefined, !!session.user);
  const qc = useQueryClient();
  if (!session.loading && !session.user)
    return <Navigate to="/login" replace />;
  return (
    <div className="public-content">
      <nav className="public-nav">
        <Brand />
        <Go to="/vacancies">Все вакансии</Go>
      </nav>
      <main>
        <Data query={q}>
          {(r) => (
            <>
              <Heading eyebrow="Вакансия" title={r.title}>
                {salary(r)} · В месяц до НДФЛ
              </Heading>
              <Panel>
                <p className="statement">{r.description}</p>
                {session.user?.roles.includes("candidate") && (
                  <Form
                    fields={[
                      text(
                        "cover_letter",
                        "Сопроводительное письмо",
                        false,
                        "textarea",
                      ),
                    ]}
                    label="Откликнуться"
                    submit={async (v) => {
                      await send(`/vacancies/${id}/applications`, v);
                      await invalidate(qc);
                    }}
                  >
                    <p className="muted">
                      Отклик создаёт основание для раскрытия контактов компании,
                      если вы разрешили их показ
                    </p>
                  </Form>
                )}
              </Panel>
            </>
          )}
        </Data>
      </main>
    </div>
  );
}
export function Applications({ role }: { role: "candidate" | "employer" }) {
  const q = useData(`/applications?cabinet=${role}`);
  const qc = useQueryClient();
  const vacancies = useData(
    role === "employer" ? "/me/vacancies" : "/vacancies",
  );
  return (
    <>
      <Heading eyebrow="Дополнительный путь" title="Отклики">
        Статусы помогают обеим сторонам понимать следующий шаг
      </Heading>
      <Panel>
        <Data query={q}>
          {(rows) =>
            rows.length ? (
              rows.map((r: Row) => (
                <ListRow
                  key={r.id}
                  title={
                    vacancies.data?.find((v: Row) => v.id === r.vacancy_id)
                      ?.title || "Отклик на вакансию"
                  }
                  subtitle={r.cover_letter || date(r.created_at)}
                  status={r.status}
                >
                  {!["withdrawn", "rejected"].includes(r.status) &&
                    (role === "candidate" ? (
                      <Action
                        secondary
                        run={async () => {
                          await send(`/applications/${r.id}/withdraw`);
                          await qc.resetQueries();
                        }}
                      >
                        Отозвать
                      </Action>
                    ) : (
                      <div className="actions">
                        {[
                          ["view", "Просмотрено"],
                          ["invite", "Пригласить"],
                          ["reject", "Отклонить"],
                        ].map(([a, label]) => (
                          <Action
                            key={a}
                            secondary
                            run={async () => {
                              await send(`/applications/${r.id}/${a}`);
                              await invalidate(qc);
                            }}
                          >
                            {label}
                          </Action>
                        ))}
                        <Go to={`/employer/candidates/${r.candidate_id}`}>
                          Профиль и оффер
                        </Go>
                      </div>
                    ))}
                </ListRow>
              ))
            ) : (
              <Empty title="Откликов пока нет" />
            )
          }
        </Data>
      </Panel>
    </>
  );
}
export function Assignments({ role }: { role: string }) {
  const q = useData(`/assignments?cabinet=${role}`);
  return (
    <>
      <Heading eyebrow="Актуальность профиля" title="Короткие задания">
        Практический разговор с работодателем. Оценка обновляет актуальность, но
        не меняет грейд
      </Heading>
      <Panel>
        <Data query={q}>
          {(rows) =>
            rows.length ? (
              rows.map((r: Row) => (
                <ListRow
                  key={r.id}
                  title={r.title || `Задание от ${date(r.issued_at)}`}
                  subtitle={`Срок: ${date(r.due_at)}${r.score != null ? ` · ${r.score}/${r.max_score}` : ""}`}
                  status={r.status}
                  to={`/${role}/assignments/${r.id}`}
                />
              ))
            ) : (
              <Empty title="Заданий пока нет">
                Работодатель может выдать задание из карточки кандидата
              </Empty>
            )
          }
        </Data>
      </Panel>
    </>
  );
}
export function AssignmentPage({ role }: { role: string }) {
  const { id } = useParams();
  const q = useData(`/assignments/${id}`);
  const qc = useQueryClient();
  return (
    <>
      <Heading
        eyebrow="Практический сигнал"
        title="Короткое задание"
        action={<Go to={`/${role}/assignments`}>Все задания</Go>}
      />
      <Data query={q}>
        {(r) => (
          <Panel>
            <div className="split">
              <Badge value={r.status} />
              <span>Срок: {date(r.due_at)}</span>
            </div>
            <p className="statement">{r.statement}</p>
            {r.answer_text && (
              <>
                <h3>Ответ кандидата</h3>
                <p className="statement">{r.answer_text}</p>
              </>
            )}
            {r.feedback && (
              <>
                <h3>Обратная связь</h3>
                <p>{r.feedback}</p>
                <strong>
                  {r.score}/{r.max_score}
                </strong>
              </>
            )}
            {role === "candidate" && r.status !== "scored" && (
              <Form
                fields={[
                  text("answer", "Ваш подход или решение", true, "textarea"),
                ]}
                initial={{ answer: r.answer_text }}
                label="Отправить ответ"
                submit={async (v) => {
                  await send(`/assignments/${id}/answer`, v);
                  await invalidate(qc);
                }}
              />
            )}
            {role === "employer" && r.status === "answered" && (
              <Form
                fields={[
                  {
                    ...text("score", "Оценка", true, "number"),
                    max: r.max_score,
                  },
                  text("feedback", "Обратная связь", true, "textarea"),
                ]}
                submit={async (v) => {
                  await send(`/assignments/${id}/score`, v);
                  await invalidate(qc);
                }}
              />
            )}
          </Panel>
        )}
      </Data>
    </>
  );
}
