// @vitest-environment jsdom
// With the app lock on, the lock screen is the first and only thing App draws: no page, no navigation, nothing asked of
// Runway but the lock. Unlocked, the app is back; Sign out is always there.
import { render, screen, waitFor } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Saved before anything is imported, as on a device that had the lock on when the app was opened.
vi.hoisted(() => localStorage.setItem("runway.lock", JSON.stringify({ on: true, idle: 60 })));
vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn(), currentPage: () => 0, signInUrl: () => "/auth/login" }));
vi.mock("svelte-sonner", async (real) => ({
  ...(await real<typeof import("svelte-sonner")>()),
  toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn(), warning: vi.fn(), dismiss: vi.fn() }),
}));

import { api } from "$lib/api";
import App from "./App.svelte";

const STATUS = { available: true, on: true, locked: true, idle: 60, credential_id: "AQID", device_id: "dev_1" };

beforeEach(() => { vi.mocked(api).mockReset(); });
afterEach(() => { vi.unstubAllGlobals(); });

describe("App with the app lock on", () => {
  it("draws only the lock screen, and asks Runway for nothing but the lock", async () => {
    vi.mocked(api).mockImplementation((async (path: string) => {
      if (path === "/api/lock/engage") return STATUS;
      if (path === "/api/lock/challenge") return { challenge: "AA", rp_id: "localhost", credential_id: "AQID", device_id: "dev_1" };
      throw new Error(`asked for ${path} while locked`);
    }) as never);
    render(App);
    expect(screen.getByRole("heading", { name: "Runway is locked" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Unlock" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Sign out/ })).toBeInTheDocument();
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Loading")).not.toBeInTheDocument();
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/lock/challenge", expect.anything()));
    // (jsdom has no WebAuthn, so the device's prompt fails: quietly, since the app asked on its own)
    await waitFor(() => expect(screen.getByRole("button", { name: "Unlock" })).toBeEnabled());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(vi.mocked(api).mock.calls.map((c) => c[0]).every((p) => p.startsWith("/api/lock/"))).toBe(true);
  });

  it("is back to the app once unlocked", async () => {
    vi.mocked(api).mockImplementation((async (path: string) => {
      if (path === "/api/lock/engage") return STATUS;
      if (path === "/api/lock/challenge") return { challenge: "AA", rp_id: "localhost", credential_id: "AQID", device_id: "dev_1" };
      if (path === "/api/lock/unlock") return { ...STATUS, locked: false };
      return new Promise(() => {});   // the app's own loading: left waiting
    }) as never);
    const cred = { rawId: new Uint8Array([1]).buffer, response: { clientDataJSON: new Uint8Array([1]).buffer, authenticatorData: new Uint8Array([1]).buffer, signature: new Uint8Array([1]).buffer } };
    const get = vi.fn().mockRejectedValueOnce(Object.assign(new Error("no tap"), { name: "NotAllowedError" })).mockResolvedValue(cred);
    vi.stubGlobal("navigator", { ...navigator, credentials: { get } });
    render(App);
    await waitFor(() => expect(get).toHaveBeenCalledOnce());   // offered on its own, turned down (no tap first)
    await userEvent.click(await screen.findByRole("button", { name: "Unlock" }));
    await waitFor(() => expect(screen.queryByRole("heading", { name: "Runway is locked" })).not.toBeInTheDocument());
    expect(screen.getByLabelText("Loading")).toBeInTheDocument();
  });
});
