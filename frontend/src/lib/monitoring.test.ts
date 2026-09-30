// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";

const init = vi.fn();
const addEventProcessor = vi.fn();
const form = { appendToDom: vi.fn(), open: vi.fn() };
const createForm = vi.fn(async () => form);
const named = (name: string) => vi.fn((opts?: unknown) => ({ name, opts }));
vi.mock("@sentry/browser", () => ({
  init, addEventProcessor, getFeedback: () => ({ createForm }),
  breadcrumbsIntegration: named("Breadcrumbs"), browserTracingIntegration: named("BrowserTracing"),
  browserProfilingIntegration: named("BrowserProfiling"), replayIntegration: named("Replay"),
  consoleLoggingIntegration: named("ConsoleLogging"), feedbackIntegration: named("Feedback"),
}));

type Integration = { name: string; opts?: Record<string, unknown> };
type Options = Record<string, unknown> & {
  dsn: string; integrations: (d: Integration[]) => Integration[];
  beforeSend: (e: Record<string, unknown>) => Record<string, unknown>;
  beforeSendSpan: (s: { name: string; attributes: Record<string, unknown> }) => { name: string; attributes: Record<string, unknown> };
  beforeSendLog: (l: { message: string; attributes?: Record<string, unknown> }) => { message: string; attributes?: Record<string, unknown> };
  beforeBreadcrumb: (c: { data?: Record<string, string> }) => { data?: Record<string, string> };
};
const cfg = { dsn: "https://k@o.ingest/1", environment: "prod", release: "1.2.3" };
const all = { ...cfg, traces: 0.5, profiles: 1, replays: 0.1, replays_on_error: 1, logs: true, feedback: true };

/** A fresh copy of the module (it starts only once), started with `config`; returns what Sentry.init was given. */
async function started(config: typeof cfg | typeof all) {
  vi.resetModules();
  init.mockClear();
  addEventProcessor.mockClear();
  const m = await import("./monitoring");
  await m.startMonitoring(config);
  return { m, opts: init.mock.calls[0][0] as Options };
}

describe("startMonitoring", () => {
  it("does nothing when Runway isn't set up for error reports", async () => {
    const { startMonitoring } = await import("./monitoring");
    await startMonitoring(null);
    await startMonitoring(undefined);
    expect(init).not.toHaveBeenCalled();
  });

  it("starts once, however often it's asked", async () => {
    const { m } = await started(cfg);
    await m.startMonitoring(cfg);
    expect(init).toHaveBeenCalledOnce();
    expect(init.mock.calls[0][0]).toMatchObject({ dsn: cfg.dsn, environment: "prod", release: "1.2.3" });
  });

  it("turns on only errors unless Runway says otherwise", async () => {
    const { m, opts } = await started(cfg);
    expect(opts).toMatchObject({ tracesSampleRate: 0, profileSessionSampleRate: 0, replaysSessionSampleRate: 0, replaysOnErrorSampleRate: 0 });
    expect(opts.integrations([{ name: "Dedupe" }]).map((i) => i.name)).toEqual(["Dedupe", "Breadcrumbs"]);
    expect(m.feedbackAvailable()).toBe(false);
  });

  it("turns on tracing, profiling, replays, logs and feedback when asked", async () => {
    const { m, opts } = await started(all);
    expect(opts).toMatchObject({ tracesSampleRate: 0.5, profileSessionSampleRate: 1, profileLifecycle: "trace",
                                 replaysSessionSampleRate: 0.1, replaysOnErrorSampleRate: 1 });
    const list = opts.integrations([]);
    expect(list.map((i) => i.name)).toEqual(["Breadcrumbs", "BrowserTracing", "BrowserProfiling", "Replay", "ConsoleLogging", "Feedback"]);
    expect(list.find((i) => i.name === "Replay")?.opts).toMatchObject({
      maskAllText: true, maskAllInputs: true, blockAllMedia: true, networkDetailAllowUrls: [], useCompression: false });
    expect(list.find((i) => i.name === "Feedback")?.opts).toMatchObject({ autoInject: false, showName: false, showEmail: false, enableScreenshot: false });
    expect(list.find((i) => i.name === "ConsoleLogging")?.opts).toEqual({ levels: ["warn", "error"] });
    expect(m.feedbackAvailable()).toBe(true);
  });

  it("never profiles without tracing", async () => {
    const { opts } = await started({ ...all, traces: 0 });
    expect(opts.profileSessionSampleRate).toBe(0);
    expect(opts.integrations([]).map((i) => i.name)).not.toContain("BrowserProfiling");
  });

  it("sends trace headers to Runway only", async () => {
    const { opts } = await started(all);
    const targets = opts.tracePropagationTargets as (RegExp | string)[];
    const matches = (url: string) => targets.some((t) => (typeof t === "string" ? url.startsWith(t) : t.test(url)));
    expect(matches("/api/state")).toBe(true);
    expect(matches(location.origin + "/api/state")).toBe(true);
    expect(matches("https://production.plaid.com/link")).toBe(false);
    expect(matches("//evil.example/x")).toBe(false);
    expect(matches(`https://evil.example/?next=${location.origin}/api`)).toBe(false);   // Runway's address, but not first
    expect(matches(location.origin + ".evil.example/x")).toBe(false);
  });

  it("names a page by its hash, not what's searched on it", async () => {
    const { opts } = await started(all);
    const tracing = opts.integrations([]).find((i) => i.name === "BrowserTracing")!;
    const rename = tracing.opts!.beforeStartSpan as (o: { name: string; op: string }) => { name: string };
    location.hash = "#budget/recurring?q=rent";
    expect(rename({ name: "/", op: "navigation" }).name).toBe("/#budget/recurring");
    location.hash = "";
    expect(rename({ name: "/", op: "pageload" }).name).toBe("/#overview");
  });

  describe("what it sends", () => {
    let opts: Options;
    beforeEach(async () => { ({ opts } = await started(all)); });

    it("drops the query string from the page URL (it holds what you searched for) and the user", () => {
      const e = opts.beforeSend({ request: { url: "https://runway.test/#transactions?q=rent", headers: { a: "b" } }, user: { id: 1 }, message: "boom" });
      expect(e.request).toEqual({ url: "https://runway.test/#transactions" });
      expect(e).not.toHaveProperty("user");
      expect(e.message).toBe("boom");
    });

    it("leaves an event without a request alone", () => {
      expect(opts.beforeSend({ message: "x" })).toEqual({ message: "x" });
    });

    it("cleans URLs in navigation and fetch breadcrumbs", () => {
      const c = opts.beforeBreadcrumb({ data: { url: "https://runway.test/api/tx?q=a", from: "https://runway.test/#a?x=1", to: "https://runway.test/#b?y=2" } });
      expect(c.data).toEqual({ url: "https://runway.test/api/tx", from: "https://runway.test/#a", to: "https://runway.test/#b" });
    });

    it("drops console and click breadcrumbs, which would carry what's on screen", () => {
      const kept = opts.integrations([{ name: "Console" }, { name: "Breadcrumbs" }, { name: "Dedupe" }]);
      expect(kept.map((i) => i.name).slice(0, 2)).toEqual(["Dedupe", "Breadcrumbs"]);
    });

    it("keeps searches and merchants' names out of traces", () => {
      expect(opts).not.toHaveProperty("beforeSendTransaction");   // ignored while spans are streamed
      const fetch = opts.beforeSendSpan({ name: "GET /api/transactions?q=rent&limit=50", attributes: {
        "url.full": "https://runway.test/api/transactions?q=rent", "http.query": "q=rent", "sentry.op": "http.client", "http.response.status_code": 200 } });
      expect(fetch).toEqual({ name: "GET /api/transactions?[Filtered]", attributes: {
        "url.full": "https://runway.test/api/transactions", "sentry.op": "http.client", "http.response.status_code": 200 } });
      const logo = opts.beforeSendSpan({ name: "https://runway.test/api/merchants/name%3Astarbucks/logo", attributes: {
        "url.full": "https://runway.test/api/merchants/name:starbucks/logo", "sentry.segment.name": "/#transactions" } });
      expect(JSON.stringify(logo)).not.toContain("starbucks");
      expect(logo.name).toBe("https://runway.test/api/merchants/{id}/logo");
    });

    it("cleans console logs, and feedback and replay events", () => {
      const log = opts.beforeSendLog({ message: "fetch /api/transactions?q=rent failed", attributes: { "sentry.message.parameter.0": "https://u:p@x.test/a?b=1" } });
      expect(JSON.stringify(log)).not.toMatch(/rent|u:p|b=1/);
      const processor = addEventProcessor.mock.calls[0][0] as (e: Record<string, unknown>) => Record<string, unknown>;
      const fb = processor({ type: "feedback", contexts: { feedback: { url: "https://runway.test/#transactions?q=rent", message: "It's slow" } } });
      expect(fb.contexts).toEqual({ feedback: { url: "https://runway.test/#transactions", message: "It's slow" } });
      const replay = processor({ type: "replay_event", urls: ["https://runway.test/#reports?merchant=Starbucks"] });
      expect(replay.urls).toEqual(["https://runway.test/#reports"]);
      const error = { message: "x?y=1" };
      expect(processor(error)).toBe(error);   // errors have beforeSend
    });

    it("cleans addresses in replay recordings", () => {
      const replay = opts.integrations([]).find((i) => i.name === "Replay")!;
      const clean = replay.opts!.beforeAddRecordingEvent as (e: unknown) => unknown;
      expect(clean({ type: 4, data: { href: "https://runway.test/#transactions?q=rent", width: 1 } }))
        .toEqual({ type: 4, data: { href: "https://runway.test/#transactions", width: 1 } });
      expect(JSON.stringify(clean({ type: 5, data: { tag: "performanceSpan", payload: {
        op: "navigation.push", description: "https://runway.test/#transactions?q=rent", data: { previous: "https://runway.test/#reports?m=target" } } } })))
        .not.toMatch(/rent|target/);
    });
  });

  it("opens the feedback form only when feedback is on", async () => {
    let { m } = await started(cfg);
    await m.openFeedback();
    expect(createForm).not.toHaveBeenCalled();
    ({ m } = await started(all));
    await m.openFeedback();
    expect(createForm).toHaveBeenCalledOnce();
    expect(form.appendToDom).toHaveBeenCalled();
    expect(form.open).toHaveBeenCalled();
  });
});

describe("scrubText", () => {
  it("blanks queries, credentials and merchants, and leaves prose alone", async () => {
    const { scrubText } = await import("./monitoring");
    expect(scrubText("GET /api/tx?q=rent and https://a:b@x.test/y?z=1")).toBe("GET /api/tx?[Filtered] and https://[Filtered]@x.test/y?[Filtered]");
    expect(scrubText("/api/merchants/name:costco/logo")).toBe("/api/merchants/{id}/logo");
    expect(scrubText("Really? Yes.")).toBe("Really? Yes.");
    // The app's searches are in the hash (replay keeps these addresses for navigations).
    expect(scrubText("http://localhost:8799/#transactions?q=Grocer")).toBe("http://localhost:8799/#transactions?[Filtered]");
    expect(scrubText("went /#reports/merchants?name=Target then #budget?m=1")).toBe("went /#reports/merchants?[Filtered] then #budget?[Filtered]");
    expect(scrubText(undefined)).toBeUndefined();
  });
});
