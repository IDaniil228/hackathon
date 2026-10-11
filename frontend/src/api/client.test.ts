import { describe, expect, it, vi, beforeEach } from "vitest";
import { request, setToken, clearSession } from "./client";

describe("HTTP-контракт", () => {
  beforeEach(() => {
    clearSession();
    vi.unstubAllGlobals();
  });
  it("передаёт cookie и access без хранения токена", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(new Response('{"ok":true}', { status: 200 }));
    vi.stubGlobal("fetch", fetcher);
    setToken("temporary");
    expect(await request("/me")).toEqual({ ok: true });
    expect(fetcher.mock.calls[0][1].credentials).toBe("include");
    expect(fetcher.mock.calls[0][1].headers.get("Authorization")).toBe(
      "Bearer temporary",
    );
  });
  it("не повторяет неопределённую мутацию автоматически", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(new Response('{"detail":"Вход"}', { status: 401 }));
    vi.stubGlobal("fetch", fetcher);
    await expect(
      request("/needs", { method: "POST", body: "{}" }),
    ).rejects.toThrow("Вход");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});
