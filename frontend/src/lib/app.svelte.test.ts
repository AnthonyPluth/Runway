// @vitest-environment jsdom
import { toast } from "svelte-sonner";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("./categories.svelte", () => ({ loadCategories: vi.fn().mockResolvedValue([]) }));
vi.mock("./monitoring", () => ({ startMonitoring: vi.fn().mockResolvedValue(undefined) }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

import { api, newPage } from "./api";
import { loadCategories } from "./categories.svelte";
import { startMonitoring } from "./monitoring";
import { app, boot, editing, refreshState, reload, route, syncOnVisit, whenBooted } from "./app.svelte";
import type { AppState } from "./types";

const state = (extra: Partial<AppState> = {}): AppState => ({ connected: true, ...extra });
beforeEach(() => { vi.mocked(api).mockReset(); vi.mocked(newPage).mockClear(); app.state = null; app.bootError = ""; });
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

describe("state and reload", () => {
  it("refreshState keeps the reply across page changes", async () => {
    vi.mocked(api).mockResolvedValue(state({ version: "1" }));
    await refreshState();
    expect(app.state?.version).toBe("1");
    expect(api).toHaveBeenCalledWith("/api/state", { keep: true });
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
    // you were at 400 when reload() looked; the redrawn page is back at the top by the time the next frame runs
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
    expect(api).toHaveBeenCalledWith("/api/sync/auto", { method: "POST" });
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
    await vi.advanceTimersByTimeAsync(3000);   // still syncing
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
    whenBooted(late);   // already booted: straight away
    expect(late).toHaveBeenCalledOnce();
    await boot();       // a second boot only refreshes state
    expect(loadCategories).toHaveBeenCalledOnce();
  });
});
