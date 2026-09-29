// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";

const init = vi.fn();
vi.mock("@sentry/browser", () => ({ init, breadcrumbsIntegration: vi.fn(() => ({ name: "Breadcrumbs" })) }));

import { startMonitoring } from "./monitoring";

type Options = {
  dsn: string; integrations: (d: { name: string }[]) => { name: string }[];
  beforeSend: (e: Record<string, unknown>) => Record<string, unknown>;
  beforeBreadcrumb: (c: { data?: Record<string, string> }) => { data?: Record<string, string> };
};
let captured: Options;
const cfg = { dsn: "https://k@o.ingest/1", environment: "prod", release: "1.2.3" };

describe("startMonitoring", () => {
  it("does nothing when Runway isn't set up for error reports", async () => {
    await startMonitoring(null);
    await startMonitoring(undefined);
    expect(init).not.toHaveBeenCalled();
  });

  it("starts once, however often it's asked", async () => {
    await startMonitoring(cfg);
    await startMonitoring(cfg);
    expect(init).toHaveBeenCalledOnce();
    captured = init.mock.calls[0][0] as Options;   // kept: mocks are cleared between tests
    expect(captured).toMatchObject({ dsn: cfg.dsn, environment: "prod", release: "1.2.3" });
  });

  describe("what it sends", () => {
    const opts = () => captured;

    it("drops the query string from the page URL (it holds what you searched for) and the user", () => {
      const e = opts().beforeSend({ request: { url: "https://runway.test/#transactions?q=rent", headers: { a: "b" } }, user: { id: 1 }, message: "boom" });
      expect(e.request).toEqual({ url: "https://runway.test/#transactions" });
      expect(e).not.toHaveProperty("user");
      expect(e.message).toBe("boom");
    });

    it("leaves an event without a request alone", () => {
      expect(opts().beforeSend({ message: "x" })).toEqual({ message: "x" });
    });

    it("cleans URLs in navigation and fetch breadcrumbs", () => {
      const c = opts().beforeBreadcrumb({ data: { url: "https://runway.test/api/tx?q=a", from: "https://runway.test/#a?x=1", to: "https://runway.test/#b?y=2" } });
      expect(c.data).toEqual({ url: "https://runway.test/api/tx", from: "https://runway.test/#a", to: "https://runway.test/#b" });
    });

    it("drops console and click breadcrumbs, which would carry what's on screen", () => {
      const kept = opts().integrations([{ name: "Console" }, { name: "Breadcrumbs" }, { name: "Dedupe" }]);
      expect(kept.map((i) => i.name)).toEqual(["Dedupe", "Breadcrumbs"]);
    });
  });
});
