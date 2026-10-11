import { ApiError, send } from "./client";

type Entry = {
  key: string;
  fingerprint: string;
  state: string;
  operation?: string;
};
const prefix = "scepka-action:";
let identity = "";
const memory = new Map<string, Entry>();
export const setIdentity = (value: string) => {
  identity = value;
};
function read(key: string): Entry | undefined {
  try {
    const value = sessionStorage.getItem(key);
    return value ? JSON.parse(value) : undefined;
  } catch {
    return memory.get(key);
  }
}
function write(key: string, value: Entry) {
  memory.set(key, value);
  try {
    sessionStorage.setItem(key, JSON.stringify(value));
  } catch {}
}
function canonical(value: any): string {
  return JSON.stringify(value, (_key, item) =>
    item && typeof item === "object" && !Array.isArray(item)
      ? Object.fromEntries(
          Object.entries(item).sort(([a], [b]) => a.localeCompare(b)),
        )
      : item,
  );
}
export async function action(path: string, body: Record<string, unknown> = {}) {
  const owner = identity;
  const scope = `${prefix}${owner}:${path}:${body.candidate_id || body.survey_session_id || ""}`;
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(canonical(body)),
  );
  const fingerprint = [...new Uint8Array(digest)]
    .map((x) => x.toString(16).padStart(2, "0"))
    .join("");
  if (identity !== owner)
    throw new Error("Аккаунт изменился; повторите действие после входа");
  const previous = read(scope);
  if (
    previous &&
    !["completed", "failed"].includes(previous.state) &&
    previous.fingerprint !== fingerprint
  )
    throw new Error(
      "Исход предыдущего действия ещё не подтверждён. Проверьте историю или состояние операции",
    );
  const entry: Entry =
    previous &&
    previous.fingerprint === fingerprint &&
    !["completed", "failed"].includes(previous.state)
      ? previous
      : { key: crypto.randomUUID(), fingerprint, state: "pending" };
  write(scope, entry);
  try {
    const result = await send(path, body, "POST", entry.key);
    entry.operation = result.state ? result.id : undefined;
    entry.state = result.state ? "pending" : "completed";
    if (owner === identity) write(scope, entry);
    return result;
  } catch (error) {
    if (error instanceof ApiError && error.status >= 400 && error.status < 500)
      entry.state = "failed";
    if (owner === identity) write(scope, entry);
    throw error;
  }
}
export function updateOperation(operation: { id: string; state: string }) {
  try {
    for (let n = 0; n < sessionStorage.length; n++) {
      const key = sessionStorage.key(n);
      if (key?.startsWith(`${prefix}${identity}:`)) {
        const entry = read(key);
        if (entry?.operation === operation.id) {
          entry.state =
            operation.state === "succeeded"
              ? "completed"
              : operation.state === "failed"
                ? "failed"
                : "pending";
          write(key, entry);
        }
      }
    }
  } catch {
    for (const [key, entry] of memory)
      if (entry.operation === operation.id)
        write(key, {
          ...entry,
          state:
            operation.state === "succeeded"
              ? "completed"
              : operation.state === "failed"
                ? "failed"
                : "pending",
        });
  }
}
export function clearActions() {
  memory.clear();
  try {
    for (const key of Object.keys(sessionStorage))
      if (key.startsWith(prefix)) sessionStorage.removeItem(key);
  } catch {}
  identity = "";
}
