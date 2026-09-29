// Talking to Runway's server. Reads belong to the page that asked for them: moving to another page cancels them,
// and a cancelled read never answers, so a slow reply can't draw the old page over the new one. `keep` opts out.

let pageLoads = new AbortController();

/** Called by the router when the page changes. */
export function newPage(): void {
  pageLoads.abort();
  pageLoads = new AbortController();
}

export class ApiError extends Error {
  constructor(message: string, readonly status: number) { super(message); }
}

type Options = { method?: "GET" | "POST" | "DELETE"; body?: unknown; keep?: boolean };

export async function api<T = unknown>(path: string, opts: Options = {}): Promise<T> {
  const init: RequestInit = { method: opts.method ?? "GET", headers: {} };
  const headers = init.headers as Record<string, string>;
  if (init.method !== "GET") headers["X-Runway"] = "1";   // Runway refuses state changes without it (CSRF)
  if (opts.body !== undefined) { headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opts.body); }
  const page = init.method === "GET" && !opts.keep ? pageLoads : null;
  if (page) init.signal = page.signal;
  let res: Response;
  try { res = await fetch(path, init); }
  catch (err) { if (page?.signal.aborted) return new Promise(() => {}); throw err; }
  if (res.status === 401) {   // signed out (session expired): sign in, then come back here
    location.href = "/auth/login?next=" + encodeURIComponent(location.pathname + location.hash);
    throw new ApiError("Signing you in again…", 401);
  }
  const data = await res.json().catch(() => ({}));
  if (page?.signal.aborted) return new Promise(() => {});
  if (!res.ok) throw new ApiError(data.error || `Request failed (${res.status})`, res.status);
  return data as T;
}
