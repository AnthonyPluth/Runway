import { describe, expect, it, vi } from "vitest";
import amazon from "../../../extension/amazon.js?raw";
import background from "../../../extension/background.js?raw";
import carta from "../../../extension/carta.js?raw";
import costco from "../../../extension/costco.js?raw";
import frames from "../../../extension/frames.js?raw";
import manifest from "../../../extension/manifest.json?raw";
import page from "../../../extension/page.js?raw";
import runwayClient from "../../../extension/runway.js?raw";
import stores from "../../../extension/stores.js?raw";
import target from "../../../extension/target.js?raw";
import util from "../../../extension/util.js?raw";

const FILES: Record<string, string> = {
  "page.js": page, "util.js": util, "runway.js": runwayClient, "stores.js": stores, "frames.js": frames,
  "amazon.js": amazon, "target.js": target, "costco.js": costco, "carta.js": carta,
};

type Api = { base: string; key: string };
interface Worker {
  storeUrl: (url: unknown) => string;
  checkedArgs: (cmd: string, args: unknown[]) => unknown[];
  costcoWindows: (since: string | null, days: number, today?: Date) => [Date, Date][];
  costcoSignedOut: (url: string | null) => boolean;
  costcoDate: (d: Date) => string;
  targetHistoryUrl: (tpl: string, api: Api, type: string, page: number, size: number) => string;
  targetUrl: (tpl: string, api: Api, order: string) => string;
  targetApiFromRequests: () => { base: string; key: string; from: string } | null;
  cartaDays: (v: unknown) => number;
  inParallel: <T>(items: T[], width: number, fn: (item: T) => Promise<void>) => Promise<void>;
  withTimeout: <T>(p: Promise<T>, ms: number, message: string, ErrorType?: new (m: string) => Error) => Promise<T>;
  stopsImport: (e: unknown) => boolean;
  HiddenUnavailable: new (m: string) => Error;
  TARGET_OLD_HISTORY: string;
  requested: (url: string) => void;
}

function load(): Worker {
  let onRequest: (d: { url: string }) => void = () => {};
  const noEvent = { addListener() {}, removeListener() {} };
  const chrome = {
    runtime: { id: "runway", onConnect: noEvent, onMessage: noEvent },
    storage: { local: { get: async () => ({}), set: async () => {}, remove: async () => {} } },
    webRequest: { onBeforeRequest: { addListener: (f: typeof onRequest) => { onRequest = f; } } },
    offscreen: { createDocument: async () => {} },
  };
  const body = Object.values(FILES).join("\n") + `
    return { storeUrl, checkedArgs, costcoWindows, costcoSignedOut, costcoDate, targetHistoryUrl, targetUrl, targetApiFromRequests,
             cartaDays, inParallel, withTimeout, stopsImport, HiddenUnavailable, TARGET_OLD_HISTORY };`;
  const w = new Function("chrome", body)(chrome) as Worker;
  return { ...w, requested: (url) => onRequest({ url }) };
}
const w = load();
const ymd = ([a, b]: [Date, Date]) => [a, b].map((d) => `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`).join("..");
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

describe("how the files fit together", () => {
  it("lists the same files in the manifest (Firefox) and in importScripts (Chrome), in the same order", () => {
    const m = JSON.parse(manifest).background;
    const fromManifest = (m.scripts as string[]).filter((f) => f !== "background.js");
    const imports = /importScripts\(([^)]*)\)/.exec(background)![1].match(/"([^"]+)"/g)!.map((s) => s.slice(1, -1));
    expect(fromManifest).toEqual(imports);
    expect(Object.keys(FILES)).toEqual(imports);
    expect(m.scripts.at(-1)).toBe("background.js");
    expect(m.service_worker).toBe("background.js");
  });
});

describe("storeUrl", () => {
  it.each([
    "https://www.amazon.com/gp/your-account/order-details?orderID=1",
    "https://www.target.com/orders",
    "https://api.target.com/guest_order_aggregations/v1/order_history?key=abc",
    "https://www.costco.com/myaccount",
    "https://ecom-api.costco.com/ebusiness/order/v1/orders/graphql",
    "https://carta.com/",
    "https://app.carta.com/investors/",
    "https://a.b.carta.com/x",
  ])("allows %s", (url) => {
    expect(w.storeUrl(url)).toBe(new URL(url).href);
  });

  it.each([
    ["not https", "http://www.amazon.com/orders"],
    ["javascript:", "javascript:alert(1)"],
    ["data:", "data:text/html,hi"],
    ["a relative address", "/orders"],
    ["not an address", "not a url"],
    ["an empty string", ""],
    ["a sign-in in the address", "https://user:pw@www.amazon.com/"],
    ["a user name only", "https://user@www.amazon.com/"],
    ["a store's name as a sub-domain of another host", "https://www.amazon.com.evil.example/"],
    ["a store's name in the path", "https://evil.example/www.amazon.com"],
    ["a store's name in the user part", "https://www.amazon.com@evil.example/"],
    ["a look-alike host", "https://www.amazon.com-orders.example/"],
    ["a look-alike without a dot", "https://evilcarta.com/"],
    ["a look-alike of Carta's sub-domains", "https://app.carta.com.evil.example/"],
    ["a host that isn't listed", "https://smile.amazon.com/"],
    ["another Amazon", "https://www.amazon.co.uk/"],
    ["the bare store domain (only www is allowed)", "https://amazon.com/"],
    ["another Target host", "https://redsky.target.com/"],
    ["another Costco host", "https://www.costco.ca/"],
    ["a store's host on another scheme", "wss://www.target.com/"],
    ["an IP address", "https://127.0.0.1/"],
  ])("refuses %s", (_why, url) => {
    expect(() => w.storeUrl(url)).toThrow(/isn't an Amazon, Target, Costco or Carta address/);
  });

  it("names the refused address, cut to 80 characters", () => {
    const long = "http://evil.example/" + "a".repeat(200);
    let message = "";
    try { w.storeUrl(long); } catch (e) { message = (e as Error).message; }
    expect(message).toBe(`Runway won't read ${long.slice(0, 80)}: it isn't an Amazon, Target, Costco or Carta address.`);
  });

  it("returns the normalised address", () => {
    expect(w.storeUrl("https://WWW.Target.com/a b")).toBe("https://www.target.com/a%20b");
  });
});

describe("checkedArgs", () => {
  it("checks the address of a command that goes somewhere, and keeps the other arguments", () => {
    for (const cmd of ["fetch", "go", "costcoFetch"]) {
      expect(w.checkedArgs(cmd, ["https://www.target.com/x", { a: 1 }, "z"])).toEqual(["https://www.target.com/x", { a: 1 }, "z"]);
      expect(() => w.checkedArgs(cmd, ["https://evil.example/", {}])).toThrow(/Runway won't read/);
      expect(() => w.checkedArgs(cmd, [])).toThrow(/Runway won't read undefined/);
    }
  });

  it("passes the other commands' arguments through", () => {
    const args = ["not an address"];
    expect(w.checkedArgs("html", args)).toBe(args);
    expect(w.checkedArgs("calls", args)).toBe(args);
  });
});

describe("costcoSignedOut", () => {
  it.each([
    "https://www.costco.com/LogonForm?x=1",
    "https://www.costco.com/LogoffView",
    "https://signin.costco.com/oauth2/authorize",
    "https://www.costco.com/logonform",
  ])("is true for %s", (url) => expect(w.costcoSignedOut(url)).toBe(true));

  it.each([
    "https://www.costco.com/myaccount",
    "https://www.costco.com/orders-and-purchases",
    "",
    null,
  ])("is false for %s", (url) => expect(w.costcoSignedOut(url)).toBe(false));
});

describe("costcoDate", () => {
  it("is month without a leading zero, day with one", () => {
    expect(w.costcoDate(new Date(2025, 8, 1))).toBe("9/01/2025");
    expect(w.costcoDate(new Date(2025, 11, 25))).toBe("12/25/2025");
  });
});

describe("costcoWindows", () => {
  const today = new Date(2025, 8, 10);

  it("covers since..today in stretches of at most `days`, newest first", () => {
    expect(w.costcoWindows("2025-06-01", 90, today).map(ymd)).toEqual(["2025-6-13..2025-9-10", "2025-6-1..2025-6-12"]);
  });

  it("is one window when since is within the stretch, and starts on `since`", () => {
    expect(w.costcoWindows("2025-09-01", 90, today).map(ymd)).toEqual(["2025-9-1..2025-9-10"]);
    expect(w.costcoWindows("2025-09-10", 90, today).map(ymd)).toEqual(["2025-9-10..2025-9-10"]);
  });

  it("has no gaps or overlaps between windows", () => {
    const ws = w.costcoWindows("2024-01-15", 30, today);
    expect(ws.length).toBeGreaterThan(10);
    for (let i = 1; i < ws.length; i++) {
      const [from] = ws[i - 1];
      const dayBefore = new Date(from.getFullYear(), from.getMonth(), from.getDate() - 1);
      expect(ws[i][1]).toEqual(dayBefore);
    }
    expect(ws.at(-1)![0]).toEqual(new Date(2024, 0, 15));
    expect(ws.every(([a, b]) => Math.round((b.getTime() - a.getTime()) / 864e5) <= 29)).toBe(true);
  });

  it("goes back 180 days without a (valid) since", () => {
    for (const since of [null, "", "yesterday", "25-01-01"]) {
      const ws = w.costcoWindows(since, 90, today);
      expect(ws.at(-1)![0]).toEqual(new Date(2025, 8, 10 - 180));
      expect(ws[0][1]).toEqual(today);
    }
  });

  it("reads the date part of a longer timestamp", () => {
    expect(w.costcoWindows("2025-09-05T10:00:00", 90, today).map(ymd)).toEqual(["2025-9-5..2025-9-10"]);
  });

  it("is empty when since is after today", () => {
    expect(w.costcoWindows("2025-09-11", 90, today)).toEqual([]);
  });

  it("stops at 60 windows (fifteen years of 90 days)", () => {
    expect(w.costcoWindows("1990-01-01", 90, today)).toHaveLength(60);
  });

  it("defaults to today's date", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2025, 8, 10, 15, 30));
    try {
      expect(w.costcoWindows("2025-09-08", 90).map(ymd)).toEqual(["2025-9-8..2025-9-10"]);
    } finally { vi.useRealTimers(); }
  });
});

describe("Target addresses", () => {
  const api = { base: "https://api.target.com/guest_order_aggregations/v1", key: "a b&c" };

  it("targetHistoryUrl fills in a template, encoding the key", () => {
    expect(w.targetHistoryUrl("{base}/h?p={page}&n={size}&t={type}&key={key}", api, "STORE", 3, 100))
      .toBe("https://api.target.com/guest_order_aggregations/v1/h?p=3&n=100&t=STORE&key=a%20b%26c");
  });

  it("targetHistoryUrl builds the older address when Runway doesn't say", () => {
    expect(w.targetHistoryUrl(w.TARGET_OLD_HISTORY, { ...api, key: "k" }, "ONLINE", 1, 10))
      .toBe("https://api.target.com/guest_order_aggregations/v1/order_history?page_number=1&page_size=10" +
        "&order_purchase_type=ONLINE&pending_order=true&shipt_status=true&key=k");
  });

  it("targetUrl fills in an order's address, encoding the number", () => {
    expect(w.targetUrl("{base}/o/{order}?key={key}", api, "12/3 4")).toBe(
      "https://api.target.com/guest_order_aggregations/v1/o/12%2F3%204?key=a%20b%26c");
  });

  it("targetApiFromRequests prefers the newest order-API call with a key, else another call's key", () => {
    expect(w.targetApiFromRequests()).toBeNull();
    w.requested("https://api.target.com/other/v1/thing?key=k1");
    expect(w.targetApiFromRequests()).toEqual({ base: "https://api.target.com/guest_order_aggregations/v1", key: "k1", from: "requests" });
    w.requested("https://api.target.com/x/guest_order_aggregations/v1/order_history?key=k2&page=1");
    w.requested("https://api.target.com/other/v1/thing?key=k3");
    w.requested("https://api.target.com/nokey");
    expect(w.targetApiFromRequests()).toEqual({ base: "https://api.target.com/x/guest_order_aggregations/v1", key: "k2", from: "requests" });
  });
});

describe("cartaDays", () => {
  it("allows daily, weekly and monthly, and falls back to daily", () => {
    expect([1, 7, 30, "7", "30"].map(w.cartaDays)).toEqual([1, 7, 30, 7, 30]);
    expect([undefined, 0, 2, "x", null].map(w.cartaDays)).toEqual([1, 1, 1, 1, 1]);
  });
});

describe("inParallel", () => {
  it("runs fn over every item, at most `width` at a time", async () => {
    let active = 0, peak = 0;
    const seen: number[] = [];
    await w.inParallel([1, 2, 3, 4, 5, 6, 7], 3, async (n) => {
      peak = Math.max(peak, ++active);
      await sleep(5);
      seen.push(n);
      active--;
    });
    expect(peak).toBe(3);
    expect(seen.sort()).toEqual([1, 2, 3, 4, 5, 6, 7]);
  });

  it("does nothing for no items, and never starts more workers than items", async () => {
    const fn = vi.fn(async () => {});
    await w.inParallel([], 4, fn);
    expect(fn).not.toHaveBeenCalled();
    let active = 0, peak = 0;
    await w.inParallel([1, 2], 10, async () => { peak = Math.max(peak, ++active); await sleep(5); active--; });
    expect(peak).toBe(2);
  });

  it("stops taking new items after the first failure, and rejects with it", async () => {
    const started: number[] = [];
    const run = w.inParallel([1, 2, 3, 4, 5, 6], 2, async (n) => {
      started.push(n);
      await sleep(5);
      if (n === 2) throw new Error("boom");
    });
    await expect(run).rejects.toThrow("boom");
    expect(started).toEqual([1, 2, 3]);
  });
});

describe("withTimeout", () => {
  it("passes the promise's value through", async () => {
    await expect(w.withTimeout(Promise.resolve(7), 1000, "late")).resolves.toBe(7);
  });

  it("passes its rejection through", async () => {
    await expect(w.withTimeout(Promise.reject(new Error("no")), 1000, "late")).rejects.toThrow("no");
  });

  it("rejects with the message once the time has passed", async () => {
    vi.useFakeTimers();
    try {
      const result = expect(w.withTimeout(new Promise(() => {}), 500, "stalled")).rejects.toThrow("stalled");
      await vi.advanceTimersByTimeAsync(500);
      await result;
    } finally { vi.useRealTimers(); }
  });

  it("uses the error type it's given", async () => {
    vi.useFakeTimers();
    try {
      const result = expect(w.withTimeout(new Promise(() => {}), 10, "gone", w.HiddenUnavailable))
        .rejects.toBeInstanceOf(w.HiddenUnavailable);
      await vi.advanceTimersByTimeAsync(10);
      await result;
    } finally { vi.useRealTimers(); }
  });

  it("clears its timer once the promise settles", async () => {
    vi.useFakeTimers();
    try {
      await w.withTimeout(Promise.resolve(1), 1000, "late");
      expect(vi.getTimerCount()).toBe(0);
    } finally { vi.useRealTimers(); }
  });
});

describe("stopsImport", () => {
  it("is true for the errors that end a store's import", () => {
    expect(w.stopsImport(new w.HiddenUnavailable("x"))).toBe(true);
    expect(w.stopsImport(Object.assign(new Error("x"), { signin: true }))).toBe(true);
    expect(w.stopsImport(Object.assign(new Error("x"), { limited: true }))).toBe(true);
    expect(w.stopsImport({ code: "signin" })).toBe(true);
    expect(w.stopsImport({ code: "robot" })).toBe(true);
  });

  it("is false for an error that only skips one order", () => {
    expect(w.stopsImport(new Error("x"))).toBe(false);
    expect(w.stopsImport({ code: "other" })).toBe(false);
    expect(w.stopsImport(null)).toBe(false);
    expect(w.stopsImport(undefined)).toBe(false);
  });
});
