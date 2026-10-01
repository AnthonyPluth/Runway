// The web app's reports to Sentry, only when Runway is set up for them (SENTRY_DSN; see runway/monitoring.py), and
// each feature only when it's switched on there. Loaded on demand, so nothing of Sentry's is fetched otherwise.
// What's sent never holds what's on the page: no console or click breadcrumbs (they'd hold amounts and names), no query
// strings, no merchant names in addresses, and replays with every piece of text, input and image masked.
import type { SentryConfig } from "./types";

let started = false;
let feedbackOn = false;

// Merchant logos are at /api/merchants/<id>/logo, and a merchant's id can be its name ("name:starbucks").
const MERCHANT_LOGO = /(\/api\/merchants\/)[^/?#\s"']+(\/logo)/g;
// An address's query string: "https://x/y?a=1", "/api/y?a=1", or the app's own "/#transactions?q=rent" and
// "#transactions?q=rent" (its searches are in the hash), but not the "?" ending a sentence.
const URL_QUERY = /((?:[a-z][a-z0-9+.-]*:\/\/[^\s?#"']*|\/[\w.~%:|/-]*)(?:#[\w/-]*)?|#[\w/-]+)\?[^\s#"']+/gi;
const USERINFO = /([a-z][a-z0-9+.-]*:\/\/)[^/\s@"']+@/gi;

/** Any text that may hold addresses: queries blanked, merchants' ids replaced, credentials removed. */
export const scrubText = <T>(text: T): T =>
  typeof text === "string"
    ? (text.replace(USERINFO, "$1[Filtered]@").replace(URL_QUERY, "$1?[Filtered]").replace(MERCHANT_LOGO, "$1{id}$2") as T)
    : text;

/** "/#transactions?q=rent" → "/#transactions": the page, not what was searched. */
export const pathOnly = (url: string | undefined) => {
  if (!url) return url;
  try {
    const u = new URL(url, location.origin);
    return (u.origin + u.pathname).replace(MERCHANT_LOGO, "$1{id}$2") + u.hash.split("?")[0];
  } catch { return scrubText(url.split("?")[0]); }
};

/** A page's name in traces: "/#budget/recurring", whatever was searched on it. */
export const pageName = () => "/#" + ((location.hash || "#overview").slice(1).split("?")[0] || "overview");

type Data = Record<string, unknown> | undefined;
const URL_KEYS = ["url", "url.full", "http.url", "from", "to"];
const cleanData = (data: Data) => {
  if (!data) return;
  for (const k of ["http.query", "http.fragment", "url.query", "url.fragment"]) delete data[k];
  for (const [k, v] of Object.entries(data)) {
    if (typeof v === "string") data[k] = URL_KEYS.includes(k) ? pathOnly(v) : scrubText(v);
  }
};

/** A span as it's sent (the SDK streams them): its name and attributes, cleaned of searches and merchants' names. */
export function scrubSpan<S extends { name: string; attributes?: Data }>(span: S): S {
  span.name = scrubText(span.name);
  cleanData(span.attributes);
  return span;
}

/** Of whoever the SDK has on an event, only the id: the code Runway gave it (startMonitoring), never a name or address. */
export const onlyId = <E extends { user?: { id?: unknown } | null }>(event: E): E => {
  const id = event.user?.id;
  delete event.user;
  if (typeof id === "string" && id) event.user = { id };
  return event;
};

type Event = {
  type?: string; request?: { url?: string }; user?: { id?: unknown } | null; urls?: string[];
  contexts?: { feedback?: { url?: string; message?: string } };
};

/** Feedback and replays (they don't go through beforeSend): addresses cleaned wherever they're kept. */
function scrubEvent<E extends Event>(event: E): E {
  if (event.request) event.request = { url: pathOnly(event.request.url) };
  onlyId(event);
  if (event.contexts?.feedback?.url) event.contexts.feedback.url = pathOnly(event.contexts.feedback.url);
  if (event.urls) event.urls = event.urls.map((u) => pathOnly(u) ?? u);
  return event;
}

// Replay's recording: the page's address (meta), and the network and navigation entries it keeps (custom events).
type RecordingEvent = { type: number; data?: { href?: string; payload?: { description?: string; data?: Data } } };
function scrubRecording(e: RecordingEvent): RecordingEvent {
  if (e.type === 4 && e.data?.href) e.data.href = pathOnly(e.data.href);
  if (e.type === 5 && e.data?.payload) {
    e.data.payload.description = scrubText(e.data.payload.description);
    cleanData(e.data.payload.data);
  }
  return e;
}

export async function startMonitoring(cfg: SentryConfig | null | undefined): Promise<void> {
  if (!cfg || started) return;
  started = true;
  const Sentry = await import("@sentry/browser");
  const traces = cfg.traces ?? 0;
  const replays = (cfg.replays ?? 0) > 0 || (cfg.replays_on_error ?? 0) > 0;
  feedbackOn = !!cfg.feedback;
  Sentry.init({
    dsn: cfg.dsn, environment: cfg.environment, release: cfg.release,
    dataCollection: { userInfo: false, cookies: false, httpHeaders: false, httpBodies: [], urlQueryParams: false },
    tracesSampleRate: traces,
    // Trace headers only to Runway itself, so its server's trace joins the page's.
    tracePropagationTargets: [/^\/(?!\/)/, new RegExp("^" + location.origin.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "(/|$)")],
    // The browser's own profiler (Chrome and Edge), while a sampled trace runs; the page asks for it with a header.
    profileSessionSampleRate: traces ? (cfg.profiles ?? 0) : 0, profileLifecycle: "trace",
    replaysSessionSampleRate: cfg.replays ?? 0, replaysOnErrorSampleRate: cfg.replays_on_error ?? 0,
    // No console breadcrumbs (Console) or click breadcrumbs (Breadcrumbs' dom): they'd carry what's on screen.
    integrations: (defaults) => [
      ...defaults.filter((i) => i.name !== "Console" && i.name !== "Breadcrumbs"),
      Sentry.breadcrumbsIntegration({ dom: false }),
      // Pages are the hash (#budget/recurring), so name page loads and navigations by it, never by what's searched.
      ...(traces ? [Sentry.browserTracingIntegration({ beforeStartSpan: (o) => ({ ...o, name: pageName() }) })] : []),
      ...(traces && cfg.profiles ? [Sentry.browserProfilingIntegration()] : []),
      ...(replays ? [Sentry.replayIntegration({
        // Every piece of text, every input and every image masked: a replay shows the layout and what was clicked.
        maskAllText: true, maskAllInputs: true, blockAllMedia: true,
        maskAttributes: ["title", "placeholder", "aria-label", "alt", "href", "value", "label", "data-value"],
        networkDetailAllowUrls: [],   // no request or response bodies or headers
        // The compression worker would be a blob: script, which the page's Content-Security-Policy doesn't allow.
        useCompression: false,
        beforeAddRecordingEvent: (e) => scrubRecording(e as RecordingEvent) as typeof e,
      })] : []),
      // Console warnings and errors as Sentry Logs (their text is Runway's own messages, cleaned like the rest).
      ...(cfg.logs ? [Sentry.consoleLoggingIntegration({ levels: ["warn", "error"] })] : []),
      // "Send feedback" in Settings opens it: anonymous (the name and email fields are hidden), and no screenshot.
      ...(cfg.feedback ? [Sentry.feedbackIntegration({
        autoInject: false, showName: false, showEmail: false, enableScreenshot: false, showBranding: false,
        colorScheme: "system", formTitle: "Send feedback", messagePlaceholder: "What's wrong, or what would make Runway better?",
      })] : []),
    ],
    beforeSend(event) {
      if (event.request) event.request = { url: pathOnly(event.request.url) };
      onlyId(event);
      // An error's text can name an address or a row (a failed fetch says where), so it's cleaned like the server's.
      if (event.message) event.message = scrubText(event.message);
      const entry: { message?: string; formatted?: string } | undefined = event.logentry;   // "formatted" isn't in the SDK's type
      if (entry?.message) entry.message = scrubText(entry.message);
      if (entry?.formatted) entry.formatted = scrubText(entry.formatted);
      for (const exc of event.exception?.values ?? []) if (exc.value) exc.value = scrubText(exc.value);
      return event;
    },
    // Spans are streamed (SDK 11's default), so they're cleaned here; beforeSendTransaction would never run.
    beforeSendSpan: (span) => scrubSpan(span as Parameters<typeof scrubSpan>[0]) as typeof span,
    beforeSendLog(log) {
      log.message = scrubText(log.message);
      for (const [k, v] of Object.entries(log.attributes ?? {})) if (typeof v === "string") log.attributes![k] = scrubText(v);
      return log;
    },
    beforeBreadcrumb(crumb) {
      if (crumb.data?.url) crumb.data.url = pathOnly(crumb.data.url);
      if (crumb.data?.to) crumb.data.to = pathOnly(crumb.data.to);
      if (crumb.data?.from) crumb.data.from = pathOnly(crumb.data.from);
      return crumb;
    },
  });
  // Who's signed in, as Runway's code for them: errors, traces, profiles and replays count people, not "anonymous".
  if (cfg.user_id) Sentry.setUser({ id: cfg.user_id });
  // Feedback and replays don't go through beforeSend; this runs for every event.
  Sentry.addEventProcessor((event) => (event.type === "feedback" || event.type === "replay_event" ? scrubEvent(event as Event) as typeof event : event));
}

/** Whether "Send feedback" can be offered (Runway is set up for it, and reporting has started). */
export const feedbackAvailable = () => started && feedbackOn;

/** Open Sentry's feedback form. */
export async function openFeedback(): Promise<void> {
  if (!feedbackAvailable()) return;
  const Sentry = await import("@sentry/browser");
  const form = await Sentry.getFeedback()?.createForm();
  form?.appendToDom();
  form?.open();
}
