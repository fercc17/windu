// Thin, typed client for the ISReq analytics API (FastAPI backend, issue #40).
// All paths go through /api, which Vite proxies to the backend in dev. The Python
// metrics layer is reused verbatim; this is just the wire format.

const BASE = "/api";

async function getJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, init);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return (await res.json()) as T;
}

export interface Health {
  status: string;
  issues: number;
  last_sync: string | null;
}

/** Backend liveness + data freshness. Throws if the API is not reachable yet. */
export function getHealth(): Promise<Health> {
  return getJson<Health>("/health");
}

export interface Identity {
  email: string;
  display_name: string;
  is_is_member: boolean;
  roles: string[];
  roster_size: number;
}

/** Stub identity — drives IS-only tab visibility. `as` impersonates for demo. */
export function getMe(as?: string): Promise<Identity> {
  return getJson<Identity>("/me/" + (as ? `?as=${encodeURIComponent(as)}` : ""));
}

export interface SyncSourceResult {
  source: string;
  label: string;
  ok: boolean;
  count: number | null;
  log: string[];
}
export interface SyncResult {
  ok: boolean;
  results: SyncSourceResult[];
}

/** Trigger a read-only incremental sync of both sources (the "Fetch data" action). */
export function runSync(): Promise<SyncResult> {
  return getJson<SyncResult>("/sync", { method: "POST" });
}

/** Fetch a page's payload. `path` is a full "/api/..." endpoint (see pages.ts);
 *  `params` are the View controls (cadence/scope/pr_mp + page-local group/top_n).
 *  Endpoints ignore params they don't declare, so the global trio is safe to send
 *  to every page. */
export async function getPage(
  path: string,
  params?: Record<string, string | number>,
): Promise<Record<string, unknown>> {
  const qs = params
    ? Object.entries(params)
        .filter(([, v]) => v !== undefined && v !== "")
        .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
        .join("&")
    : "";
  const res = await fetch(qs ? `${path}?${qs}` : path);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return (await res.json()) as Record<string, unknown>;
}
