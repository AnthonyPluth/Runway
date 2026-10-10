// @vitest-environment jsdom
// The app lock's state machine (lib/lock.svelte.ts): what a launch starts as, waiting to load anything until it's
// unlocked, locking again after time away (and covering the page meanwhile), the server's 423, unlocking (and when the
// device or the server says no), the server having no lock for this sign-in any more, and forgetting it.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn(), dismiss: vi.fn() }) }));
vi.mock("$lib/webauthn", async (real) => ({ ...(await real<typeof import("./webauthn")>()), signChallenge: vi.fn() }));

import { api, newPage } from "$lib/api";
import { toast } from "svelte-sonner";
import { signChallenge } from "$lib/webauthn";

type Lock = typeof import("./lock.svelte");
const STATUS = { available: true, on: true, locked: true, idle: 60, credential_id: "Y3JlZA" };
const ANSWER = { credential_id: "Y3JlZA", client_data: "e30", authenticator_data: "AA", signature: "AA" };

/** A fresh copy of the module, as a launch would load it, with this saved on the device. */
async function launch(saved: unknown): Promise<Lock> {
  localStorage.clear();
  if (saved !== undefined) localStorage.setItem("runway.lock", typeof saved === "string" ? saved : JSON.stringify(saved));
  vi.resetModules();
  return await import("./lock.svelte");
}

function serve(routes: Record<string, unknown>) {
  vi.mocked(api).mockImplementation((async (path: string) => {
    const r = routes[path];
    if (r instanceof Error) throw r;
    if (r === undefined) throw new Error(`unexpected ${path}`);
    return typeof r === "function" ? r() : r;
  }) as never);
}
const refused = (message: string, status: number) => Object.assign(new Error(message), { status });

beforeEach(() => { vi.mocked(api).mockReset(); vi.mocked(newPage).mockReset(); vi.mocked(signChallenge).mockReset(); vi.mocked(toast).mockReset(); });
afterEach(() => localStorage.clear());

describe("launching", () => {
  it("starts locked, and loads nothing until it's unlocked, when the device has the lock on", async () => {
    const m = await launch({ on: true, idle: 300 });
    expect(m.lock.phase).toBe("locked");
    expect(m.lock.idle).toBe(300);
    const load = vi.fn();
    m.whenUnlocked(load);
    expect(load).not.toHaveBeenCalled();
    serve({ "/api/lock/engage": STATUS, "/api/lock/challenge": { challenge: "Y2g", rp_id: "runway.example", credential_id: "Y3JlZA" },
            "/api/lock/unlock": { ...STATUS, locked: false } });
    vi.mocked(signChallenge).mockResolvedValue(ANSWER);
    expect(await m.unlock()).toBe(true);
    expect(m.lock.phase).toBe("unlocked");
    expect(load).toHaveBeenCalledOnce();
    // The launch locked the server too, before asking for the challenge.
    expect(vi.mocked(api).mock.calls.map((c) => c[0])).toEqual(["/api/lock/engage", "/api/lock/challenge", "/api/lock/unlock"]);
    m.whenUnlocked(load);   // later ones run at once
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("starts as before, loading straight away, without the lock (or with storage it can't read)", async () => {
    for (const saved of [undefined, { on: false, idle: 60 }, "not json", { on: "yes" }]) {
      const m = await launch(saved);
      expect(m.lock.phase).toBe("off");
      const load = vi.fn();
      m.whenUnlocked(load);
      expect(load).toHaveBeenCalledOnce();
    }
    const m = await launch({ on: true, idle: 42 });   // a time it doesn't offer: the default
    expect(m.lock.idle).toBe(m.DEFAULT_IDLE);
  });

  it("stops locking, and says so, when this sign-in has no lock (signed in again)", async () => {
    const m = await launch({ on: true, idle: 60 });
    const load = vi.fn();
    m.whenUnlocked(load);
    serve({ "/api/lock/engage": { ...STATUS, on: false, locked: false, credential_id: null } });
    expect(await m.lockScreenShown()).toBe(false);
    expect(m.lock.phase).toBe("off");
    expect(localStorage.getItem("runway.lock")).toBeNull();
    expect(load).toHaveBeenCalledOnce();
    expect(toast).toHaveBeenCalledWith(expect.stringContaining("App lock is off on this device"));
  });

  it("stops locking when there's no sign-in any more, but stays locked when Runway can't be reached", async () => {
    let m = await launch({ on: true, idle: 60 });
    serve({ "/api/lock/engage": refused("App lock needs Runway’s sign-in", 400) });
    expect(await m.lockScreenShown()).toBe(false);
    expect(m.lock.phase).toBe("off");
    m = await launch({ on: true, idle: 60 });
    serve({ "/api/lock/engage": refused("Can’t reach Runway.", 0) });
    expect(await m.lockScreenShown()).toBe(true);
    expect(m.lock.phase).toBe("locked");
    expect(m.lock.error).toBe("Can’t reach Runway.");
    expect(m.lock.launch).toBe(true);   // the server is asked again with the next try
  });
});

describe("unlocking", () => {
  async function locked(): Promise<Lock> {
    const m = await launch({ on: true, idle: 60 });
    m.lock.launch = false;
    serve({ "/api/lock/challenge": { challenge: "Y2g", rp_id: "runway.example", credential_id: "Y3JlZA" },
            "/api/lock/unlock": { ...STATUS, locked: false } });
    return m;
  }

  it("stays locked, saying why, when Face ID is cancelled; quietly when the app asked on its own", async () => {
    const m = await locked();
    vi.mocked(signChallenge).mockRejectedValue(Object.assign(new Error("The operation either timed out or was not allowed."), { name: "NotAllowedError" }));
    expect(await m.unlock()).toBe(false);
    expect(m.lock.phase).toBe("locked");
    expect(m.lock.error).toBe("Cancelled, or it took too long. Try again.");
    expect(await m.unlock(true)).toBe(false);
    expect(m.lock.error).toBe("");
    expect(vi.mocked(api).mock.calls.some((c) => c[0] === "/api/lock/unlock")).toBe(false);
  });

  it("stays locked when the server doesn't accept the answer", async () => {
    const m = await locked();
    vi.mocked(signChallenge).mockResolvedValue(ANSWER);
    serve({ "/api/lock/challenge": { challenge: "Y2g", rp_id: "runway.example", credential_id: "Y3JlZA" },
            "/api/lock/unlock": refused("Face ID, Touch ID or the passcode didn’t check out. Try again.", 400) });
    expect(await m.unlock()).toBe(false);
    expect(m.lock.phase).toBe("locked");
    expect(m.lock.error).toContain("didn’t check out");
    expect(m.lock.busy).toBe(false);
  });

  it("catches up after each unlock but the first", async () => {
    const m = await locked();
    const again = vi.fn();
    m.afterUnlock(again);
    vi.mocked(signChallenge).mockResolvedValue(ANSWER);
    await m.unlock();
    expect(again).not.toHaveBeenCalled();
    m.lockNow();
    await m.unlock();
    expect(again).toHaveBeenCalledOnce();
  });
});

describe("away and back", () => {
  async function unlocked(idle: number): Promise<Lock> {
    const m = await launch({ on: true, idle });
    m.turnedOn({ ...STATUS, idle, locked: false });
    vi.mocked(api).mockResolvedValue(STATUS as never);
    return m;
  }

  it("locks again after the chosen time away, and covers the page while away", async () => {
    const m = await unlocked(60);
    m.wentAway(1_000_000);
    expect(m.lock.covered).toBe(true);           // the app switcher's preview shows nothing
    expect(m.lock.phase).toBe("unlocked");
    m.cameBack(1_000_000 + 59_000);
    expect([m.lock.phase, m.lock.covered]).toEqual(["unlocked", false]);
    expect(api).not.toHaveBeenCalled();
    m.wentAway(2_000_000);
    m.cameBack(2_000_000 + 60_000);
    expect([m.lock.phase, m.lock.covered]).toEqual(["locked", false]);
    expect(newPage).toHaveBeenCalled();          // the page's reads in flight are dropped
    expect(toast.dismiss).toHaveBeenCalled();
    expect(api).toHaveBeenCalledWith("/api/lock/engage", { method: "POST", keepalive: true, background: true });
  });

  it("locks as the app goes to the background when set to Immediately", async () => {
    const m = await unlocked(0);
    m.wentAway(1_000);
    expect(m.lock.phase).toBe("locked");
    expect(api).toHaveBeenCalledWith("/api/lock/engage", expect.objectContaining({ keepalive: true }));
  });

  it("does nothing without the lock", async () => {
    const m = await launch(undefined);
    m.wentAway(0);
    m.cameBack(10_000_000);
    expect([m.lock.phase, m.lock.covered]).toEqual(["off", false]);
    m.lockNow();
    expect(m.lock.phase).toBe("off");
    expect(api).not.toHaveBeenCalled();
  });

  it("draws the lock screen when the server says it's locked, even if the device had forgotten the lock", async () => {
    const m = await launch(undefined);
    window.dispatchEvent(new Event("runway:locked"));
    expect(m.lock.phase).toBe("locked");
    expect(JSON.parse(localStorage.getItem("runway.lock")!)).toMatchObject({ on: true });
    expect(api).not.toHaveBeenCalled();          // the server already is locked: nothing to tell it
  });
});

describe("turning it off and signing out", () => {
  it("forgets the lock on this device", async () => {
    const m = await launch({ on: true, idle: 300 });
    m.turnedOff();
    expect(m.lock.phase).toBe("off");
    expect(localStorage.getItem("runway.lock")).toBeNull();
    const again = await launch({ on: true, idle: 300 });
    again.forget();   // what signing out does (lib/nav.svelte.ts signOut)
    expect(localStorage.getItem("runway.lock")).toBeNull();
  });

  it("keeps one id for this device's passkeys, so turning it on again replaces the passkey", async () => {
    const m = await launch(undefined);
    const a = m.deviceUserId(), b = m.deviceUserId();
    expect(a).toHaveLength(16);
    expect([...b]).toEqual([...a]);
  });
});
