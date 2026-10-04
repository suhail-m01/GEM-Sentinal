/** Typed API client. All calls are same-origin relative paths; Next.js
 * rewrites proxy them to the FastAPI backend. */

const TOKEN_KEY = "gemsentinel.access";
const REFRESH_KEY = "gemsentinel.refresh";

export function getTokens(): { access: string | null; refresh: string | null } {
  if (typeof window === "undefined") return { access: null, refresh: null };
  return {
    access: localStorage.getItem(TOKEN_KEY),
    refresh: localStorage.getItem(REFRESH_KEY),
  };
}

export function setTokens(access: string, refresh?: string) {
  localStorage.setItem(TOKEN_KEY, access);
  if (refresh) localStorage.setItem(REFRESH_KEY, refresh);
}

export function clearTokens() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(REFRESH_KEY);
}

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown) {
    super(typeof detail === "string" ? detail : JSON.stringify(detail));
    this.status = status;
    this.detail = detail;
  }
}

const DEVICE_KEY = "gemsentinel.device";

/** Stable per-browser device ID for trusted-device (2FA remember) - sent as
 *  X-Device-Id on every request; the backend stores only its SHA-256 hash. */
export function getDeviceId(): string | null {
  if (typeof window === "undefined") return null;
  try {
    let id = localStorage.getItem(DEVICE_KEY);
    if (!id) {
      id = (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`);
      localStorage.setItem(DEVICE_KEY, id);
    }
    return id;
  } catch {
    return null;
  }
}

/** Prefix all API paths so browser calls never clash with Next.js page routes. */
export function apiPath(path: string): string {
  return `/api${path.startsWith("/") ? path : `/${path}`}`;
}

export async function rawRequest(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  const { access } = getTokens();
  const url = apiPath(path);
  if (access) {
    headers.set("Authorization", `Bearer ${access}`);
    headers.set("X-GS-Token", access);
  }
  const lang = typeof window !== "undefined" ? localStorage.getItem("gemsentinel.locale") : null;
  if (lang) headers.set("X-Preferred-Language", lang);
  const device = getDeviceId();
  if (device) headers.set("X-Device-Id", device);
  return fetch(url, { ...init, headers, cache: "no-store" });
}

export async function api<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
  let res = await rawRequest(path, init);
  if (res.status === 401) {
    // try one refresh
    const { refresh } = getTokens();
    if (refresh && !init.body) {
      const rr = await fetch(apiPath("/auth/refresh"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refresh }),
      });
      if (rr.ok) {
        const data = await rr.json();
        setTokens(data.access_token, data.refresh_token);
        res = await rawRequest(path, init);
      }
    }
  }
  if (!res.ok) {
    let detail: unknown = await res.text();
    try {
      detail = JSON.parse(detail as string);
    } catch {
      /* keep text */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const get = <T = unknown>(path: string) => api<T>(path);
export const post = <T = unknown>(path: string, body?: unknown) =>
  api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
export const put = <T = unknown>(path: string, body?: unknown) =>
  api<T>(path, { method: "PUT", body: body === undefined ? undefined : JSON.stringify(body) });

export async function upload<T = unknown>(path: string, form: FormData): Promise<T> {
  return api<T>(path, { method: "POST", body: form });
}

export const fileUrl = (kind: string, filename: string) => `/api/files/${kind}/${filename}`;

/**
 * Download an authenticated binary (PDF report) via blob. `window.open` does
 * not carry Authorization headers, so a direct navigation 401s - this helper
 * fetches with the API client and triggers a local save.
 */
export async function downloadFile(path: string, fallbackName: string): Promise<void> {
  let res = await rawRequest(path);
  if (res.status === 401) {
    const { refresh } = getTokens();
    if (refresh) {
      const rr = await fetch(apiPath("/auth/refresh"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refresh }),
      });
      if (rr.ok) {
        const data = await rr.json();
        setTokens(data.access_token, data.refresh_token);
        res = await rawRequest(path);
      }
    }
  }
  if (!res.ok) {
    let detail: unknown = await res.text();
    try {
      detail = JSON.parse(detail as string);
    } catch {
      /* keep text */
    }
    throw new ApiError(res.status, detail);
  }
  const cd = res.headers.get("content-disposition") || "";
  const m = /filename="?([^";]+)"?/.exec(cd);
  const name = m?.[1] || fallbackName;
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}
