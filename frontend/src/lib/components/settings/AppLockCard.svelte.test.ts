// @vitest-environment jsdom
// Settings → Data → App lock: what a device that can't have one is told, turning it on (a passkey, checked by the
// server), choosing when it locks, and turning it off.
import { render, screen, waitFor } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn(), dismiss: vi.fn() }) }));
vi.mock("$lib/webauthn", async (real) => ({ ...(await real<typeof import("$lib/webauthn")>()), unsupportedReason: vi.fn(), createPasskey: vi.fn() }));

import { api } from "$lib/api";
import { lock } from "$lib/lock.svelte";
import { createPasskey, unsupportedReason } from "$lib/webauthn";
import { toast } from "svelte-sonner";
import AppLockCard from "./AppLockCard.svelte";

type Status = { available: boolean; on: boolean; locked: boolean; idle: number; credential_id: string | null; device_id: string | null };
const OFF: Status = { available: true, on: false, locked: false, idle: 60, credential_id: null, device_id: null };
const MADE = { credential_id: "AQID", client_data: "e30", authenticator_data: "AA", public_key: "MAE", alg: -7 };

let sent: { path: string; opts?: { method?: string; body?: unknown } }[] = [];
function serve(status: Status, fail: Record<string, Error> = {}) {
  vi.mocked(api).mockImplementation((async (path: string, opts?: { method?: string; body?: Record<string, unknown> }) => {
    sent.push({ path, opts });
    if (fail[path]) throw fail[path];
    if (path === "/api/lock" && opts?.method === "DELETE") return (status = { ...status, on: false, credential_id: null });
    if (path === "/api/lock") return status;
    if (path === "/api/lock/challenge") return { challenge: "AA", rp_id: "runway.example", credential_id: null };
    if (path === "/api/lock/register") return (status = { ...status, on: true, idle: opts!.body!.idle as number, credential_id: "AQID" });
    if (path === "/api/lock/settings") return (status = { ...status, idle: opts!.body!.idle as number });
    if (path === "/api/lock/engage") return { ...status, locked: true };
    throw new Error(`unexpected ${path}`);
  }) as never);
}

beforeEach(() => {
  sent = [];
  vi.mocked(api).mockReset();
  vi.mocked(unsupportedReason).mockResolvedValue("");
  vi.mocked(createPasskey).mockReset();
  localStorage.clear();
  lock.phase = "off";
});
afterEach(() => localStorage.clear());

describe("App lock settings", () => {
  it("says it needs sign-in, without anything to turn on", async () => {
    serve({ ...OFF, available: false });
    render(AppLockCard);
    expect(await screen.findByText(/App lock needs Runway’s sign-in/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Turn on/ })).not.toBeInTheDocument();
  });

  it("says a device without Face ID, Touch ID or a passcode for websites can't have it, and why https", async () => {
    serve(OFF);
    vi.mocked(unsupportedReason).mockResolvedValue("device");
    const { unmount } = render(AppLockCard);
    expect(await screen.findByText(/This device can’t use Face ID, Touch ID or a passcode/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Turn on/ })).not.toBeInTheDocument();
    unmount();
    vi.mocked(unsupportedReason).mockResolvedValue("https");
    render(AppLockCard);
    expect(await screen.findByText(/needs Runway to be opened over/)).toBeInTheDocument();
  });

  it("turns on with a passkey the server checks, at the time chosen, and remembers it on this device", async () => {
    serve(OFF);
    vi.mocked(createPasskey).mockResolvedValue(MADE);
    render(AppLockCard);
    await userEvent.selectOptions(await screen.findByLabelText(/Lock again after/), "300");
    expect(sent.some((s) => s.path === "/api/lock/settings")).toBe(false);   // nothing to save until it's on
    await userEvent.click(screen.getByRole("button", { name: "Turn on app lock" }));
    await screen.findByText("On for this device");
    expect(sent.find((s) => s.path === "/api/lock/challenge")?.opts?.body).toEqual({ purpose: "register" });
    expect(sent.find((s) => s.path === "/api/lock/register")?.opts?.body).toEqual({ ...MADE, idle: 300 });
    expect(JSON.parse(localStorage.getItem("runway.lock")!)).toEqual({ on: true, idle: 300 });
    expect(lock.phase).toBe("unlocked");
    expect(toast.success).toHaveBeenCalledWith("App lock is on for this device");
  });

  it("stays off, saying why, when the device says no", async () => {
    serve(OFF);
    vi.mocked(createPasskey).mockRejectedValue(Object.assign(new Error("x"), { name: "NotAllowedError" }));
    render(AppLockCard);
    await userEvent.click(await screen.findByRole("button", { name: "Turn on app lock" }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Cancelled, or it took too long. Try again."));
    expect(sent.some((s) => s.path === "/api/lock/register")).toBe(false);
    expect(localStorage.getItem("runway.lock")).toBeNull();
    expect(screen.getByRole("button", { name: "Turn on app lock" })).toBeEnabled();
  });

  it("changes when it locks, and turns off, on this device", async () => {
    serve({ ...OFF, on: true, idle: 60, credential_id: "AQID" });
    render(AppLockCard);
    await screen.findByText("On for this device");
    await userEvent.selectOptions(screen.getByLabelText(/Lock again after/), "0");
    await waitFor(() => expect(JSON.parse(localStorage.getItem("runway.lock")!)).toEqual({ on: true, idle: 0 }));
    expect(sent.find((s) => s.path === "/api/lock/settings")?.opts?.body).toEqual({ idle: 0 });
    await userEvent.click(screen.getByRole("button", { name: "Turn off on this device" }));
    await screen.findByRole("button", { name: "Turn on app lock" });
    expect(localStorage.getItem("runway.lock")).toBeNull();
    expect(lock.phase).toBe("off");
  });

  it("puts the time back when saving it fails", async () => {
    serve({ ...OFF, on: true, idle: 60, credential_id: "AQID" }, { "/api/lock/settings": new Error("Server down") });
    render(AppLockCard);
    await screen.findByText("On for this device");
    await userEvent.selectOptions(screen.getByLabelText(/Lock again after/), "900");
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Server down"));
    expect(screen.getByLabelText(/Lock again after/)).toHaveValue("60");
  });

  it("forgets a lock the server no longer has for this sign-in", async () => {
    localStorage.setItem("runway.lock", JSON.stringify({ on: true, idle: 60 }));
    serve(OFF);
    render(AppLockCard);
    await screen.findByRole("button", { name: "Turn on app lock" });
    expect(localStorage.getItem("runway.lock")).toBeNull();
  });
});
