// The web app's error reports to Sentry, only when Runway is set up for them (SENTRY_DSN; see runway/monitoring.py).
// Loaded on demand, so nothing of Sentry's is fetched otherwise. Reports carry the error and the page's path, never
// what's on the page: no console or click breadcrumbs (they'd hold amounts and names), no query strings.
import type { SentryConfig } from "./types";

let started = false;

/** "/#transactions?q=rent" → "/#transactions": the page, not what was searched. */
const pathOnly = (url: string | undefined) => {
  if (!url) return url;
  try {
    const u = new URL(url, location.origin);
    return u.origin + u.pathname + u.hash.split("?")[0];
  } catch { return url.split("?")[0]; }
};

export async function startMonitoring(cfg: SentryConfig | null | undefined): Promise<void> {
  if (!cfg || started) return;
  started = true;
  const Sentry = await import("@sentry/browser");
  Sentry.init({
    dsn: cfg.dsn, environment: cfg.environment, release: cfg.release,
    dataCollection: { userInfo: false, cookies: false, httpHeaders: false, httpBodies: [], urlQueryParams: false },
    // No console breadcrumbs (Console) or click breadcrumbs (Breadcrumbs' dom): they'd carry what's on screen.
    integrations: (defaults) => [...defaults.filter((i) => i.name !== "Console" && i.name !== "Breadcrumbs"),
      Sentry.breadcrumbsIntegration({ dom: false })],
    beforeSend(event) {
      if (event.request) event.request = { url: pathOnly(event.request.url) };
      delete event.user;
      return event;
    },
    beforeBreadcrumb(crumb) {
      if (crumb.data?.url) crumb.data.url = pathOnly(crumb.data.url);
      if (crumb.data?.to) crumb.data.to = pathOnly(crumb.data.to);
      if (crumb.data?.from) crumb.data.from = pathOnly(crumb.data.from);
      return crumb;
    },
  });
}
