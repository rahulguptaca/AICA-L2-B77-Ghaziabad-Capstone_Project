/** API client — all requests go to the FastAPI backend (never to the AI provider). */

const BASE = "";

class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: options.body instanceof FormData ? {} : { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || JSON.stringify(body);
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json();
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) }),
  put: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PUT", body: JSON.stringify(body) }),
  upload: <T>(path: string, form: FormData) =>
    request<T>(path, { method: "POST", body: form }),
};

export { ApiError };

/* ---------- formatting helpers (display only; engine owns the math) ---------- */

const CR = 1e7;

export function fmtCr(v: number | null | undefined, decimals = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `₹ ${(v / CR).toLocaleString("en-IN", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })} Cr`;
}

export function fmtCrPlain(v: number | null | undefined, decimals = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return (v / CR).toLocaleString("en-IN", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

export function fmtPct(v: number | null | undefined, decimals = 1, signed = false): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const s = (v * 100).toFixed(decimals);
  return `${signed && v > 0 ? "+" : ""}${s}%`;
}

export function fmtIndian(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return v.toLocaleString("en-IN");
}

export function fmtDate(iso: string | null | undefined, withTime = false): string {
  if (!iso) return "—";
  const d = new Date(iso);
  const date = d.toLocaleDateString("en-IN", { year: "numeric", month: "short", day: "2-digit" });
  if (!withTime) return date;
  const time = d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" });
  return `${date} ${time}`;
}
