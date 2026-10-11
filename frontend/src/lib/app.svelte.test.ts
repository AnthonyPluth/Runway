// @vitest-environment jsdom
import { toast } from "svelte-sonner";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./api", () => ({ api: vi.fn(), forgetReplies: vi.fn(), newPage: vi.fn() }));
vi.mock("./categories.svelte", () => ({ loadCategories: vi.fn().mockResolvedValue([]) }));
vi.mock("./monitoring", () => ({ startMonitoring: vi.fn().mockResolvedValue(undefined) }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn(), dismiss: vi.fn() }) }));
vi.mock("./deviceCache", async (real) => ({ ...(await real<typeof import("./deviceCache")>()), noteDataVersion: vi.fn() }));
vi.mock("./webauthn", async (real) => ({ ...(await real<typeof import("./webauthn")>()), signChallenge: vi.fn() }));

import { api, newPage } from "./api";
import { loadCategories } from "./categories.svelte";
import { startMonitoring } from "./monitoring";
import { dataVersionOf, noteDataVersion } from "./deviceCache";
import { app, boot, checkIn, editing, refreshState, reload, route, syncOnVisit, whenBooted } from "./app.svelte";
import { lock, unlock } from "./lock.svelte";
import { signChallenge } from "./webauthn";
import type { AppState } from "./types";

const state = (extra: Partial<AppState> = {}): AppState => ({ connected: true, ...extra });
beforeEach(() => {
  vi.mocked(api).mockReset(); vi.mocked(newPage).mockClear();
  app.state = null; app.bootError = ""; app.sessionExpired = false;
});
afterEach(() => { vi.useRealTimers(); document.body.innerHTML = ""; });

describe("routing", () => {
  const go = async (hash: string) => { location.hash = hash; window.dispatchEvent(new HashChangeEvent("hashchange")); };

  it("reads the page and sub-page from the hash", async () => {
    await go("#setup/connections");
    expect(route).toMatchObject({ page: "setup", sub: "connections" });
  });

  it("treats the old #settings name as the setup page", async () => {
    await go("#settings/rules");
    expect(route).toMatchObject({ page: "setup", sub: "rules" });
  });

  it("opens the old #investments route as a tab of Net worth", async () => {
    await go("#investments");
    expect(route).toMatchObject({ page: "networth", sub: "investments" });
    await go("#networth/investments");
    expect(route).toMatchObject({ page: "networth", sub: "investments" });
  });

  it("opens Recurring at #recurring, and the old #budget/recurring there too", async () => {
    await go("#recurring");
    expect(route).toMatchObject({ page: "recurring", sub: "" });
    await go("#budget");
    expect(route).toMatchObject({ page: "budget", sub: "" });
    await go("#budget/recurring?q=rent");
    expect(route).toMatchObject({ page: "recurring", sub: "" });
  });

  it("opens #networth/retirement as the Retirement tab of Net worth", async () => {
    await go("#networth/retirement");
    expect(route).toMatchObject({ page: "networth", sub: "retirement" });
  });

  it("ignores a query string, which belongs to the page's filters", async () => {
    await go("#transactions?q=rent");
    expect(route).toMatchObject({ page: "transactions", sub: "" });
  });

  it("opens Overview when there's no hash, and cancels the old page's reads on every change", async () => {
    vi.mocked(newPage).mockClear();
    await go("");
    expect(route.page).toBe("overview");
    expect(newPage).toHaveBeenCalled();
  });
});

describe("editing", () => {
  it("is true while a field has focus, so a redraw doesn't discard what you typed", () => {
    document.body.innerHTML = `<input id="f">`;
    expect(editing()).toBe(false);
    document.getElementById("f")!.focus();
    expect(editing()).toBe(true);
  });

  it("is true while an editor is open", () => {
    document.body.innerHTML = `<div data-editor></div>`;
    expect(editing()).toBe(true);
  });
});

describe("session expiry", () => {
  const signedOut = (background: boolean) =>
    window.dispatchEvent(new CustomEvent("runway:signed-out", { cancelable: true, detail: { background } }));

  it("lets api send you to sign in when nothing's being edited", () => {
    expect(signedOut(false)).toBe(true);
    expect(app.sessionExpired).toBe(false);
  });

  it("keeps you on the page, with the banner, while you're editing or when Runway was only checking in", () => {
    expect(signedOut(true)).toBe(false);
    expect(app.sessionExpired).toBe(true);
    app.sessionExpired = false;
    document.body.innerHTML = `<div data-editor></div>`;
    expect(signedOut(false)).toBe(false);
    expect(app.sessionExpired).toBe(true);
  });

  it("stops watching a sync once signed out", async () => {
    vi.useFakeTimers();
    vi.spyOn(console, "error").mockImplementation(() => {});
    app.state = state({ last_sync_ok: "old" });
    vi.mocked(api).mockImplementation(async (path: string) => {
      if (path === "/api/sync/auto") return { started: true } as never;
      app.sessionExpired = true;
      throw new Error("Your session expired.");
    });
    await syncOnVisit();
    await vi.advanceTimersByTimeAsync(3000);
    expect(api).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(9000);
    expect(api).toHaveBeenCalledTimes(2);
  });
});

describe("state and reload", () => {
  it("refreshState keeps the reply across page changes", async () => {
    vi.mocked(api).mockResolvedValue(state({ version: "1" }));
    await refreshState();
    expect(app.state?.version).toBe("1");
    expect(api).toHaveBeenCalledWith("/api/state", { keep: true });
    await refreshState(true);
    expect(api).toHaveBeenLastCalledWith("/api/state", { keep: true, background: true });
  });

  it("refreshState tells the device's encrypted cache which data version this is", async () => {
    const s = state({ version: "1", last_sync_ok: "2026-10-11T07:00:00-04:00" });
    vi.mocked(api).mockResolvedValue(s);
    await refreshState();
    expect(noteDataVersion).toHaveBeenLastCalledWith(dataVersionOf(s));
  });

  it("reload cancels in-flight reads and bumps the version so the page loads again", () => {
    const v = app.version;
    reload();
    expect(newPage).toHaveBeenCalled();
    expect(app.version).toBe(v + 1);
  });

  it("reload puts you back at your scroll position while the page settles", () => {
    vi.useFakeTimers();
    const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    vi.spyOn(window, "scrollY", "get").mockReturnValueOnce(400).mockReturnValue(0);
    Object.defineProperty(document.documentElement, "scrollHeight", { value: 2000, configurable: true });
    reload();
    vi.advanceTimersByTime(50);
    expect(scrollTo).toHaveBeenCalledWith(0, 400);
    vi.restoreAllMocks();
  });
});

describe("syncOnVisit", () => {
  it("does nothing more when the server says nothing needs syncing", async () => {
    vi.mocked(api).mockResolvedValue({ started: false });
    await syncOnVisit();
    expect(api).toHaveBeenCalledTimes(1);
    expect(api).toHaveBeenCalledWith("/api/sync/auto", { method: "POST", background: true });
  });

  it("survives the server being unreachable", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    vi.mocked(api).mockRejectedValue(new Error("down"));
    await expect(syncOnVisit()).resolves.toBeUndefined();
  });

  it("polls until the sync ends, then says so and loads the page again", async () => {
    vi.useFakeTimers();
    app.state = state({ syncing: false, last_sync_ok: "old" });
    const polls = [state({ syncing: true }), state({ syncing: false, last_sync_ok: "new", last_log: { ok: true, message: "3 new transactions" } })];
    vi.mocked(api).mockImplementation(async (path: string) => (path === "/api/sync/auto" ? { started: true } : polls.shift()) as never);
    await syncOnVisit();
    expect(app.state?.syncing).toBe(true);
    const v = app.version;
    await vi.advanceTimersByTimeAsync(3000);
    expect(app.version).toBe(v);
    await vi.advanceTimersByTimeAsync(3000);
    expect(toast.success).toHaveBeenCalledWith("Synced · 3 new transactions");
    expect(app.version).toBe(v + 1);
  });

  it("doesn't redraw under you when you're typing; it tells you to change page instead", async () => {
    vi.useFakeTimers();
    document.body.innerHTML = `<input id="f">`;
    document.getElementById("f")!.focus();
    app.state = state({ last_sync_ok: "old" });
    vi.mocked(api).mockImplementation(async (path: string) => (path === "/api/sync/auto" ? { started: true }
      : state({ syncing: false, last_sync_ok: "new", last_log: { ok: true, message: "done" } })) as never);
    await syncOnVisit();
    const v = app.version;
    await vi.advanceTimersByTimeAsync(3000);
    expect(toast).toHaveBeenCalledWith("Synced · done. Change page to see the new data.");
    expect(app.version).toBe(v);
  });
});

describe("boot", () => {
  it("shows why when Runway can't be reached, and doesn't start anything", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    vi.mocked(api).mockRejectedValue(new Error("Failed to fetch"));
    await boot();
    expect(app.bootError).toBe("Failed to fetch");
    expect(loadCategories).not.toHaveBeenCalled();
  });

  it("loads state, categories and error reporting once, and runs whenBooted callbacks", async () => {
    vi.mocked(api).mockImplementation(async (path: string) => (path === "/api/sync/auto" ? { started: false } : state({ sentry: { dsn: "d", environment: "e", release: "r" } })) as never);
    const early = vi.fn();
    whenBooted(early);
    expect(early).not.toHaveBeenCalled();
    await boot();
    expect(app.bootError).toBe("");
    expect(startMonitoring).toHaveBeenCalledWith({ dsn: "d", environment: "e", release: "r" });
    expect(loadCategories).toHaveBeenCalledOnce();
    expect(early).toHaveBeenCalledOnce();
    const late = vi.fn();
    whenBooted(late);
    expect(late).toHaveBeenCalledOnce();
    await boot();
    expect(loadCategories).toHaveBeenCalledOnce();
  });
});

describe("checking in", () => {
  it("picks up changes every minute without sending you to sign in", async () => {
    vi.mocked(api).mockImplementation(async (path: string) => (path === "/api/sync/auto" ? { started: false } : state()) as never);
    await boot();
    vi.mocked(api).mockClear();
    checkIn();
    expect(api).toHaveBeenCalledWith("/api/state", { keep: true, background: true });
  });

  it("stops, and doesn't sync on coming back to the tab, once signed out", async () => {
    vi.mocked(api).mockImplementation(async (path: string) => (path === "/api/sync/auto" ? { started: false } : state()) as never);
    await boot();
    document.dispatchEvent(new Event("visibilitychange"));
    expect(api).toHaveBeenCalledWith("/api/sync/auto", { method: "POST", background: true });
    vi.mocked(api).mockClear();
    app.sessionExpired = true;
    document.dispatchEvent(new Event("visibilitychange"));
    checkIn();
    expect(api).not.toHaveBeenCalled();
  });
});

describe("the app lock", () => {
  it("asks Runway nothing while locked, and catches up once unlocked", async () => {
    vi.mocked(api).mockImplementation(async (path: string) => (path === "/api/sync/auto" ? { started: false } : state()) as never);
    await boot();
    vi.mocked(api).mockClear();
    lock.phase = "locked";
    checkIn();
    document.dispatchEvent(new Event("visibilitychange"));
    window.dispatchEvent(new Event("online"));
    expect(api).not.toHaveBeenCalled();
    vi.mocked(api).mockImplementation((async (path: string) => {
      if (path === "/api/lock/challenge") return { challenge: "AA", rp_id: "localhost", credential_id: "AQID" };
      if (path === "/api/lock/unlock") return { available: true, on: true, locked: false, idle: 60, credential_id: "AQID", device_id: "dev_1" };
      return state();
    }) as never);
    vi.mocked(signChallenge).mockResolvedValue({ answer: { credential_id: "AQID", client_data: "e30", authenticator_data: "AA", signature: "AA" }, prf: null });
    lock.launch = false;
    expect(await unlock()).toBe(true);
    await vi.waitFor(() => expect(api).toHaveBeenCalledWith("/api/state", { keep: true, background: true }));
    lock.phase = "off";
  });
});
