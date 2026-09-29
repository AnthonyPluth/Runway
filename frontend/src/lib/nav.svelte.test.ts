// @vitest-environment jsdom
import { toast } from "svelte-sonner";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AppState } from "./types";

vi.mock("./api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: { error: vi.fn() } }));

import { api } from "./api";
import { route } from "./app.svelte";
import { currentPage, signOut, signedInUser, syncStatus } from "./nav.svelte";

const s = (x: Partial<AppState> = {}): AppState => ({ connected: true, ...x });

describe("syncStatus", () => {
  beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date("2026-03-10T15:00:00")); });
  afterEach(() => vi.useRealTimers());

  it("says nothing before the state has loaded", () => {
    expect(syncStatus(null)).toEqual({ text: "", tone: "", title: "" });
  });

  it("shows a running sync first", () => {
    expect(syncStatus(s({ syncing: true, last_log: { ok: false } })).tone).toBe("busy");
  });

  it("flags a failed sync with the reason on hover", () => {
    expect(syncStatus(s({ last_log: { ok: false, message: "Token expired" } })))
      .toEqual({ text: "Last sync failed", tone: "bad", title: "Token expired" });
  });

  it("shows the time when the last good sync was today", () => {
    const r = syncStatus(s({ last_sync_ok: "2026-03-10T09:05:00" }));
    expect(r.text).toBe("Up to date · 9:05 AM");
    expect(r.tone).toBe("");
  });

  it("shows the date when the last good sync was on an earlier day", () => {
    expect(syncStatus(s({ last_sync_ok: "2026-03-08T09:05:00" })).text).toBe("Up to date · Mar 8");
  });

  it("tells a connected bank that hasn't synced from one that isn't connected", () => {
    expect(syncStatus(s())).toMatchObject({ text: "Not synced yet", tone: "busy" });
    expect(syncStatus(s({ connected: false }))).toMatchObject({ text: "Bank not connected", tone: "bad" });
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
