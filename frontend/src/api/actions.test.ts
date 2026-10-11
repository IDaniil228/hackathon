import { beforeEach, expect, it, vi } from "vitest";
import { action, updateOperation, setIdentity } from "./actions";

let values: Map<string, string>;
beforeEach(() => {
  values = new Map();
  vi.stubGlobal("sessionStorage", {
    get length() {
      return values.size;
    },
    key: (n: number) => [...values.keys()][n] ?? null,
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
  });
  setIdentity("account");
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(
        new Response('{"id":"operation","state":"queued"}', { status: 202 }),
      ),
    ),
  );
});

it("сохраняет ключ до отправки и повторяет то же действие без копии личного текста", async () => {
  await action("/invitations", { message: "Private-XYZ" });
  await action("/invitations", { message: "Private-XYZ" });
  const calls = vi.mocked(fetch).mock.calls;
  expect(new Headers(calls[0][1]?.headers).get("Idempotency-Key")).toBe(
    new Headers(calls[1][1]?.headers).get("Idempotency-Key"),
  );
  expect([...values.values()].join()).not.toContain("Private-XYZ");
});

it("не создаёт новое действие, пока исход прежнего не известен", async () => {
  await action("/needs/one/runs", {});
  await expect(action("/needs/one/runs", { changed: true })).rejects.toThrow(
    "предыдущ",
  );
  expect(fetch).toHaveBeenCalledTimes(1);
  updateOperation({ id: "operation", state: "succeeded" });
  await action("/needs/one/runs", { changed: true });
  expect(fetch).toHaveBeenCalledTimes(2);
});
