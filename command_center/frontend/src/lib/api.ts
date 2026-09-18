/**
 * API client — same-origin, cookie session, CSRF header on every mutation.
 * No secrets live here: the browser only ever holds a session cookie the
 * server issued and a CSRF token that is useless without it.
 */
export class ApiError extends Error {
  status: number;
  detail: any;
  constructor(status: number, detail: any) {
    super(typeof detail === "string" ? detail : detail?.detail || `HTTP ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

function csrfToken(): string {
  const m = document.cookie.match(/(?:^|;\s*)jcc_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

const BASE = (import.meta.env.BASE_URL || "/").replace(/\/$/, "");

async function request<T>(method: string, path: string, body?: any, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const isForm = body instanceof FormData;
  if (body !== undefined && !isForm) headers["Content-Type"] = "application/json";
  if (method !== "GET") headers["X-CSRF-Token"] = csrfToken();
  let res: Response;
  try {
    res = await fetch(BASE + path, {
      method, headers, credentials: "same-origin",
      body: body === undefined ? undefined : isForm ? body : JSON.stringify(body), ...init,
    });
  } catch {
    throw new ApiError(0, "Network error — the server is unreachable");
  }
  if (res.status === 401 && !path.startsWith("/api/auth/login")) {
    window.dispatchEvent(new CustomEvent("jarvis:unauthorized"));
  }
  const text = await res.text();
  let data: any = text;
  try { data = text ? JSON.parse(text) : null; } catch { /* keep text */ }
  if (!res.ok) throw new ApiError(res.status, data);
  return data as T;
}

export const api = {
  get: <T = any>(path: string, init?: RequestInit) => request<T>("GET", path, undefined, init),
  post: <T = any>(path: string, body?: any, init?: RequestInit) => request<T>("POST", path, body ?? {}, init),
  patch: <T = any>(path: string, body?: any) => request<T>("PATCH", path, body ?? {}),
  put: <T = any>(path: string, body?: any) => request<T>("PUT", path, body ?? {}),
  del: <T = any>(path: string) => request<T>("DELETE", path),
  upload: <T = any>(path: string, form: FormData) => request<T>("POST", path, form),
  base: BASE,
};

export interface SSEEvent { type: string; id?: number; data: any; }

/** POST and read the SSE body. Returns a stop() function. */
export function streamPost(path: string, body: any, onEvent: (ev: SSEEvent) => void,
                           onDone: (err?: Error) => void): () => void {
  const ctrl = new AbortController();
  (async () => {
    let res: Response;
    try {
      res = await fetch(BASE + path, {
        method: "POST", credentials: "same-origin", signal: ctrl.signal,
        headers: { "Content-Type": "application/json", Accept: "text/event-stream", "X-CSRF-Token": csrfToken() },
        body: JSON.stringify(body),
      });
    } catch (e: any) {
      if (e?.name !== "AbortError") onDone(new ApiError(0, "Network error"));
      else onDone();
      return;
    }
    if (!res.ok || !res.body) {
      let detail: any = await res.text();
      try { detail = JSON.parse(detail); } catch { /* text */ }
      if (res.status === 401) window.dispatchEvent(new CustomEvent("jarvis:unauthorized"));
      onDone(new ApiError(res.status, detail));
      return;
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    let eventName = "";
    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let idx: number;
        while ((idx = buf.indexOf("\n")) >= 0) {
          const line = buf.slice(0, idx).replace(/\r$/, "");
          buf = buf.slice(idx + 1);
          if (line.startsWith("event:")) eventName = line.slice(6).trim();
          else if (line.startsWith("data:")) {
            try {
              const parsed = JSON.parse(line.slice(5));
              onEvent({ type: parsed.type || eventName, id: parsed.id, data: parsed.data ?? parsed });
            } catch { /* ignore */ }
          } else if (line === "") eventName = "";
        }
      }
      onDone();
    } catch (e: any) {
      onDone(e?.name === "AbortError" ? undefined : e);
    }
  })();
  return () => ctrl.abort();
}
