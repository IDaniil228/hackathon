let token = "";
let refreshFlight: Promise<void> | null = null;
export const setToken = (value: string) => {
  token = value;
};
export const clearSession = () => {
  token = "";
};

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: unknown,
  ) {
    super(
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail
              .map((x) => `${x.loc?.slice(1).join(".")}: ${x.msg}`)
              .join("; ")
          : JSON.stringify(detail),
    );
  }
}

function csrf() {
  return typeof document === "undefined"
    ? ""
    : decodeURIComponent(
        document.cookie
          .split("; ")
          .find((x) => x.startsWith("fsp_csrf="))
          ?.slice(9) || "",
      );
}

export async function request<T = any>(
  path: string,
  options: RequestInit = {},
  retry = true,
): Promise<T> {
  const headers = new Headers(options.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (options.body && !(options.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  if (path === "/auth/refresh" || path === "/auth/logout")
    headers.set("X-CSRF-Token", csrf());
  const response = await fetch(`/api${path}`, {
    ...options,
    credentials: "include",
    headers,
    cache: "no-store",
  });
  if (
    response.status === 401 &&
    retry &&
    (!options.method || options.method === "GET") &&
    !path.startsWith("/auth/")
  ) {
    await refresh();
    return request(path, options, false);
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(
      response.status,
      body.detail || `Сервис ответил ${response.status}`,
    );
  }
  if (response.headers.get("content-type")?.includes("application/pdf"))
    return (await response.blob()) as T;
  return response.status === 204 ? (undefined as T) : response.json();
}

export async function refresh() {
  if (!refreshFlight)
    refreshFlight = request("/auth/refresh", { method: "POST" }, false)
      .then((result) => {
        setToken(result.access_token);
      })
      .finally(() => {
        refreshFlight = null;
      });
  return refreshFlight;
}

export const send = <T = any>(
  path: string,
  data: unknown = {},
  method = "POST",
  key?: string,
) =>
  request<T>(path, {
    method,
    body: JSON.stringify(data),
    headers: key ? { "Idempotency-Key": key } : {},
  });

export async function download(path: string, name = "profile.pdf") {
  const blob = await request<Blob>(path);
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
