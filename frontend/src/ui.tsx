import { useState, type ReactNode, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  AlertCircle,
  Check,
  LoaderCircle,
  Plus,
} from "lucide-react";
import { request } from "./api/client";

export type Row = Record<string, any>;
export type Field = {
  name: string;
  label: string;
  type?: string;
  required?: boolean;
  options?: { value: string | number; label: string }[];
  hint?: string;
  min?: number;
  max?: number;
};
export const text = (
  name: string,
  label: string,
  required = false,
  type = "text",
): Field => ({ name, label, required, type });
export const options = (
  name: string,
  label: string,
  values: string[][],
): Field => ({
  name,
  label,
  type: "select",
  options: values.map(([value, label]) => ({ value, label })),
});
export const formatOptions = [
  ["remote", "Удалённо"],
  ["hybrid", "Гибрид"],
  ["office", "Офис"],
];
export const date = (value: string | null) =>
  value
    ? new Date(value).toLocaleString("ru-RU", {
        dateStyle: "medium",
        timeStyle: "short",
      })
    : "Не задано";
export const salary = (row: Row) =>
  `${row.salary_min?.toLocaleString("ru-RU") || "—"} – ${row.salary_max?.toLocaleString("ru-RU") || "—"} ₽`;
export const name = (row: Row) =>
  [row.first_name, row.last_name].filter(Boolean).join(" ") || "Имя скрыто";
const labels: Record<string, string> = {
  generating: "Формируем задания",
  selecting: "Подбираем задания",
  scoring: "Проверяем ответы",
  pending: "В очереди",
  unavailable: "Недоступно",
  stale: "Данные изменились",
  sent: "Отправлено",
  viewed: "Просмотрено",
  accepted: "Принято",
  declined: "Отклонено",
  withdrawn: "Отозвано",
  expired: "Истекло",
  done: "Готово",
  queued: "В очереди",
  running: "Выполняется",
  waiting_external: "Ожидаем сервис",
  reconciling: "Сверяем результат",
  uncertain: "Нужна сверка",
  failed: "Не удалось",
  succeeded: "Завершено",
  ready: "Готово",
  scored: "Оценено",
  passed: "Уровень подтверждён",
  insufficient: "Недостаточно данных",
  in_progress: "В процессе",
  submitted: "На проверке",
  created: "Создано",
  cancelled: "Отменено",
  published: "Опубликовано",
  draft: "Черновик",
  paused: "Приостановлено",
  archived: "Архив",
  answered: "Ответ получен",
  invited: "Приглашён",
  rejected: "Отклонён",
  issued: "Выдано",
};
export function Badge({ value }: { value: string }) {
  return (
    <span
      className={`badge ${["failed", "declined", "rejected"].includes(value) ? "bad" : ""}`}
    >
      <span />
      {labels[value] || value}
    </span>
  );
}
export function Heading({
  eyebrow,
  title,
  children,
  action,
}: {
  eyebrow?: string;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <header className="page-heading">
      <div>
        {eyebrow && <p className="eyebrow">{eyebrow}</p>}
        <h1>{title}</h1>
        {children && <p className="lede">{children}</p>}
      </div>
      {action}
    </header>
  );
}
export function Panel({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return <section className={`panel ${className}`}>{children}</section>;
}
export function Empty({
  title = "Пока здесь тихо",
  children,
}: {
  title?: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-sign">[ + ]</div>
      <h3>{title}</h3>
      <p>
        {children ||
          "Новые события появятся здесь, когда вы сделаете следующий шаг"}
      </p>
    </div>
  );
}
export function ErrorBox({ error }: { error: unknown }) {
  return error ? (
    <div className="error" role="alert">
      <AlertCircle size={18} />
      <span>{error instanceof Error ? error.message : String(error)}</span>
    </div>
  ) : null;
}
export function Loading() {
  return (
    <div className="loading" role="status">
      <LoaderCircle className="spin" size={22} /> Загружаем данные
    </div>
  );
}
export function Button({
  children,
  onClick,
  secondary = false,
  disabled = false,
  type = "button",
}: {
  children: ReactNode;
  onClick?: () => void;
  secondary?: boolean;
  disabled?: boolean;
  type?: "button" | "submit";
}) {
  return (
    <button
      className={`button ${secondary ? "secondary" : ""}`}
      type={type}
      onClick={onClick}
      disabled={disabled}
    >
      {children}
    </button>
  );
}
export function Action({
  children,
  run,
  secondary = false,
  confirm,
  disabled = false,
}: {
  children: ReactNode;
  run: () => Promise<unknown>;
  secondary?: boolean;
  confirm?: string;
  disabled?: boolean;
}) {
  const [state, setState] = useState<"idle" | "busy" | "ok">("idle");
  const [error, setError] = useState<unknown>();
  return (
    <span className="action">
      <Button
        secondary={secondary}
        disabled={disabled || state === "busy"}
        onClick={async () => {
          if (confirm && !window.confirm(confirm)) return;
          setState("busy");
          setError(undefined);
          try {
            await run();
            setState("ok");
          } catch (e) {
            setError(e);
            setState("idle");
          }
        }}
      >
        {state === "busy" ? (
          <LoaderCircle className="spin" size={16} />
        ) : state === "ok" ? (
          <Check size={16} />
        ) : null}
        {children}
      </Button>
      <ErrorBox error={error} />
    </span>
  );
}
export function Form({
  fields,
  initial = {},
  submit,
  label = "Сохранить",
  children,
}: {
  fields: Field[];
  initial?: Row;
  submit: (value: Row) => Promise<unknown>;
  label?: string;
  children?: ReactNode;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const [ok, setOk] = useState(false);
  async function handle(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const values: Row = {};
    fields.forEach((f) => {
      const raw = data.get(f.name);
      if (f.type === "multi-select") values[f.name] = data.getAll(f.name);
      else if (f.type === "checkbox") values[f.name] = raw === "on";
      else if (f.type === "number" || f.type === "id")
        values[f.name] = raw === "" ? null : Number(raw);
      else if (f.type === "datetime-local")
        values[f.name] = raw ? new Date(String(raw)).toISOString() : null;
      else values[f.name] = raw === "" ? null : raw;
    });
    setBusy(true);
    setOk(false);
    setError(undefined);
    try {
      await submit(values);
      setOk(true);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <form onSubmit={handle} className="form">
      <div className="field-grid">
        {fields.map((f) => (
          <label
            key={f.name}
            className={`field ${f.type === "textarea" ? "wide" : ""} ${f.type === "checkbox" ? "check-field" : ""}`}
          >
            <span>
              {f.label}
              {f.required && (
                <span className="required" aria-hidden="true">
                  {" "}
                  *
                </span>
              )}
            </span>
            {f.type === "checkbox" ? (
              <input
                name={f.name}
                aria-label={f.label}
                type="checkbox"
                defaultChecked={!!initial[f.name]}
                required={f.required}
              />
            ) : f.options ? (
              <select
                name={f.name}
                aria-label={f.label}
                multiple={f.type === "multi-select"}
                defaultValue={
                  f.type === "multi-select"
                    ? initial[f.name] || []
                    : (initial[f.name] ?? "")
                }
                required={f.required}
              >
                {f.type !== "multi-select" && (
                  <option value="">
                    {f.required ? "Выберите" : "Не задано"}
                  </option>
                )}
                {f.options.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            ) : f.type === "textarea" ? (
              <textarea
                name={f.name}
                aria-label={f.label}
                defaultValue={initial[f.name] ?? ""}
                required={f.required}
                rows={5}
                maxLength={20000}
              />
            ) : (
              <input
                name={f.name}
                aria-label={f.label}
                type={f.type === "id" ? "number" : f.type || "text"}
                defaultValue={initial[f.name] ?? ""}
                required={f.required}
                min={f.min ?? (f.type === "number" ? 0 : undefined)}
                max={f.max}
                step={f.type === "number" ? "any" : undefined}
                autoComplete={
                  f.type === "password"
                    ? "current-password"
                    : f.name === "email"
                      ? "email"
                      : undefined
                }
              />
            )}
            {f.hint && <small>{f.hint}</small>}
          </label>
        ))}
      </div>
      {children}
      <ErrorBox error={error} />
      <div className="form-footer">
        <Button type="submit" disabled={busy}>
          {busy ? <LoaderCircle className="spin" size={16} /> : null}
          {label}
          <ArrowRight size={16} />
        </Button>
        {ok && (
          <span role="status" className="saved">
            <Check size={16} /> Изменения сохранены
          </span>
        )}
      </div>
    </form>
  );
}
export function useData<T = any>(
  path: string,
  interval?: number,
  enabled = true,
) {
  return useQuery<T>({
    queryKey: [path],
    queryFn: () => request(path),
    enabled,
    refetchInterval: interval,
    refetchOnWindowFocus: true,
  });
}
export function Data({
  query,
  children,
}: {
  query: ReturnType<typeof useData>;
  children: (data: any) => ReactNode;
}) {
  return query.isPending ? (
    <Loading />
  ) : query.error ? (
    <ErrorBox error={query.error} />
  ) : (
    <>{children(query.data)}</>
  );
}
export function RefSelect(
  name: string,
  label: string,
  rows: Row[] = [],
  required = false,
): Field {
  return {
    name,
    label,
    type: rows.length && typeof rows[0].id === "number" ? "id" : "select",
    required,
    options: rows.map((r) => ({
      value: r.id,
      label: r.name || r.title || r.code || r.slug,
    })),
  };
}
export function Go({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link to={to} className="button secondary">
      {children}
      <ArrowRight size={16} />
    </Link>
  );
}
export function ListRow({
  title,
  subtitle,
  to,
  status,
  children,
}: {
  title: string;
  subtitle?: string;
  to?: string;
  status?: string;
  children?: ReactNode;
}) {
  return (
    <div className="list-row" role="group" aria-label={title}>
      <div className="list-row-main">
        {to ? (
          <Link to={to} className="row-title">
            {title}
            <ArrowRight size={16} />
          </Link>
        ) : (
          <strong>{title}</strong>
        )}
        {subtitle && <p>{subtitle}</p>}
      </div>
      {status && <Badge value={status} />}
      {children}
    </div>
  );
}
export function AddLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link to={to} className="button">
      <Plus size={17} />
      {children}
    </Link>
  );
}
