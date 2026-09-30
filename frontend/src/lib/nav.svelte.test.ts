// @vitest-environment jsdom
import { toast } from "svelte-sonner";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AppState } from "./types";

vi.mock("./api", () => ({ api: vi.fn(), newPage: vi.fn(), session: {} }));
vi.mock("svelte-sonner", () => ({ toast: { error: vi.fn() } }));

import { api } from "./api";
import { route } from "./app.svelte";
import { balanceAsOf, currentPage, signOut, signedInUser, syncStatus } from "./nav.svelte";

const s = (x: Partial<AppState> = {}): AppState => ({ connected: true, ...x });

describe("syncStatus", () => {
  beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date("2026-03-10T15:00:00")); });
  afterEach(() => vi.useRealTimers());

  it("says nothing before the state has loaded", () => {
    expect(syncStatus(null)).toEqual({ text: "", tone: "", title: "", detail: "", href: "" });
  });

  it("shows a running sync first", () => {
    expect(syncStatus(s({ syncing: true, last_log: { ok: false } })).tone).toBe("busy");
  });

  it("flags a failed sync with the reason in view, and links to the connections", () => {
    expect(syncStatus(s({ last_log: { ok: false, message: "Token expired" } })))
      .toEqual({ text: "Last sync failed", tone: "bad", title: "Token expired", detail: "Token expired", href: "#setup/connections" });
  });

  it("shows the time when the last good sync was today", () => {
    const r = syncStatus(s({ last_sync_ok: "2026-03-10T09:05:00" }));
    expect(r.text).toBe("Up to date · 9:05 AM");
    expect(r).toMatchObject({ tone: "", detail: "", href: "", title: "Last synced Mar 10, 9:05 AM" });
  });

  it("reads the sync's UTC offset, so the time is this browser's", () => {
    // 13:05 UTC is 9:05 in New York (the tests' time zone, on daylight time by Mar 10).
    expect(syncStatus(s({ last_sync_ok: "2026-03-10T13:05:00+00:00" })).text).toBe("Up to date · 9:05 AM");
    expect(syncStatus(s({ last_sync_ok: "2026-03-10T08:05:00-06:00" })).text).toBe("Up to date · 10:05 AM");
  });

  it("shows the date when the last good sync was yesterday but still recent", () => {
    expect(syncStatus(s({ last_sync_ok: "2026-03-09T18:00:00" }))).toMatchObject({ text: "Up to date · Mar 9", tone: "" });
  });

  it("calls a sync over a day old out as old, in amber", () => {
    expect(syncStatus(s({ last_sync_ok: "2026-03-09T09:05:00" }))).toMatchObject({ text: "Updated yesterday", tone: "warn", href: "#setup/connections" });
    expect(syncStatus(s({ last_sync_ok: "2026-03-07T09:05:00" }))).toMatchObject({ text: "Updated 3 days ago", tone: "warn" });
  });

  it("warns when the sync worked but a bank needs attention, with what it said in view", () => {
    const one = syncStatus(s({ last_sync_ok: "2026-03-10T09:05:00", sync_warnings: ["Chase: log in again"] }));
    expect(one).toEqual({ text: "Synced · 1 bank needs attention", tone: "warn", title: "Last synced Mar 10, 9:05 AM\nChase: log in again",
                          detail: "Chase: log in again", href: "#setup/connections" });
    expect(syncStatus(s({ last_sync_ok: "2026-03-10T09:05:00", sync_warnings: ["a", "b"] }))).toMatchObject({ text: "Synced · 2 banks need attention", detail: "a; b" });
  });

  it("puts a failed sync ahead of an old one's warnings", () => {
    expect(syncStatus(s({ last_sync_ok: "2026-03-10T09:05:00", sync_warnings: ["a"], last_log: { ok: false, message: "Down" } })).tone).toBe("bad");
  });

  it("tells a connected bank that hasn't synced from one that isn't connected", () => {
    expect(syncStatus(s())).toMatchObject({ text: "Not synced yet", tone: "busy", href: "" });
    expect(syncStatus(s({ connected: false }))).toMatchObject({ text: "Bank not connected", tone: "bad", href: "#setup/connections" });
  });
});

describe("balanceAsOf", () => {
  it("gives the newest balance's day, with the sync's time when it synced that day", () => {
    expect(balanceAsOf(["2026-09-28", "2026-09-30", null], "2026-09-30", "2026-09-30T07:02:00-04:00"))
      .toEqual({ text: "Balance as of today, 7:02 AM", stale: false });
    expect(balanceAsOf(["2026-09-29"], "2026-09-30", "2026-09-30T07:02:00-04:00")).toEqual({ text: "Balance as of Sep\u00a029", stale: false });
  });

  it("is stale once the newest balance is from before yesterday", () => {
    expect(balanceAsOf(["2026-09-28"], "2026-09-30", null)).toEqual({ text: "Balance as of Sep\u00a028", stale: true });
  });

  it("never dates a balance after today, and says nothing without a date", () => {
    expect(balanceAsOf(["2026-10-01"], "2026-09-30")?.text).toBe("Balance as of today");
    expect(balanceAsOf([null, undefined], "2026-09-30")).toBeNull();
    expect(balanceAsOf([], "2026-09-30")).toBeNull();
  });
});

describe("signedInUser", () => {
  it("returns a real signed-in user, not the local single-user placeholder", () => {
    expect(signedInUser(s({ user: { name: "Ann", email: "a@x.com" } }))).toEqual({ name: "Ann", email: "a@x.com" });
    expect(signedInUser(s({ user: { local: true } }))).toBeNull();
    expect(signedInUser(s())).toBeNull();
    expect(signedInUser(null)).toBeNull();
  });
});

describe("currentPage", () => {
  it("shows Review as a tab of Transactions", () => {
    route.page = "review";
    expect(currentPage()).toBe("transactions");
    route.page = "budget";
    expect(currentPage()).toBe("budget");
  });
});

describe("signOut", () => {
  afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); });

  it("posts the sign-out (not a link) and follows the provider's redirect", async () => {
    const fake = { href: "" };
    vi.stubGlobal("location", fake);
    vi.mocked(api).mockResolvedValue({ redirect: "https://idp.example/logout" });
    const e = new Event("click", { cancelable: true });
    await signOut(e);
    expect(e.defaultPrevented).toBe(true);
    expect(api).toHaveBeenCalledWith("/auth/logout", { method: "POST" });
    expect(fake.href).toBe("https://idp.example/logout");
  });

  it("goes to the signed-out page when the provider has none, and reports a failure", async () => {
    const fake = { href: "" };
    vi.stubGlobal("location", fake);
    vi.mocked(api).mockResolvedValueOnce({});
    await signOut(new Event("click"));
    expect(fake.href).toBe("/auth/signed-out");
    vi.mocked(api).mockRejectedValueOnce(new Error("Server down"));
    await signOut(new Event("click"));
    expect(toast.error).toHaveBeenCalledWith("Server down");
  });
});
