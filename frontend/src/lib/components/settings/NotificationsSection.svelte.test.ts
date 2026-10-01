// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", async (orig) => ({ ...(await orig()), api: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));
vi.mock("./push", async (orig) => ({ ...(await orig()), currentSubscription: vi.fn(), pushSupported: () => true, isIOS: () => false }));

import { api } from "$lib/api";
import NotificationsSection from "./NotificationsSection.svelte";
import { currentSubscription } from "./push";
import type { PushDevice, PushInfo } from "./types";

const sub = { endpoint: "https://push/me", unsubscribe: vi.fn().mockResolvedValue(true) };
const info = (devices: PushDevice[]): PushInfo => ({ public_key: "aGVsbG8", prefs: {}, devices, recent: [] });
function setup(devices: PushDevice[], here: typeof sub | null = sub) {
  vi.mocked(api).mockImplementation(async (path: string) => (path === "/api/push" ? info(devices) : { ok: true }) as never);
  vi.mocked(currentSubscription).mockResolvedValue({ reg: {} as ServiceWorkerRegistration, sub: here as unknown as PushSubscription });
  render(NotificationsSection);
}

beforeEach(() => {
  vi.stubGlobal("isSecureContext", true);
  vi.stubGlobal("Notification", Object.assign(class {}, { permission: "granted" }));
});
afterEach(() => { vi.clearAllMocks(); vi.unstubAllGlobals(); });

describe("Settings → Notifications on this device", () => {
  it("says they're on when this device is one of yours, without offering to turn them on", async () => {
    setup([{ endpoint: "https://push/me", device: "Mac · Chrome" }]);
    expect(await screen.findByText("On for this device")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Turn on notifications" })).toBeNull();
  });

  it("turns them off on this device: Runway forgets it, and so does the browser", async () => {
    setup([{ endpoint: "https://push/me", device: "Mac · Chrome" }]);
    await userEvent.click(await screen.findByRole("button", { name: "Turn off on this device" }));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/push/unsubscribe", { method: "POST", body: { endpoint: "https://push/me" } }));
    expect(sub.unsubscribe).toHaveBeenCalled();
  });

  it("offers to turn them on when this device isn't one of yours", async () => {
    setup([{ endpoint: "https://push/other", device: "iPhone · app" }]);
    expect(await screen.findByRole("button", { name: "Turn on notifications" })).toBeInTheDocument();
    expect(screen.queryByText("On for this device")).toBeNull();
  });

  it("asks to turn them on again on a device from before sign-in", async () => {
    setup([{ endpoint: "https://push/me", device: "Mac · Chrome", unclaimed: true }]);
    expect(await screen.findByRole("button", { name: "Turn on notifications" })).toBeInTheDocument();
    expect(screen.getByText(/before there was sign-in/)).toBeInTheDocument();
    expect(screen.getByText("from before sign-in")).toBeInTheDocument();
  });

  it("turns another device off from the list, after asking", async () => {
    setup([{ endpoint: "https://push/me", device: "Mac · Chrome" }, { endpoint: "https://push/phone", device: "iPhone · app" }]);
    const offs = await screen.findAllByRole("button", { name: "Turn off" });
    await userEvent.click(offs[1]);
    expect(api).not.toHaveBeenCalledWith("/api/push/unsubscribe", expect.anything());
    await userEvent.click(screen.getByRole("button", { name: "Turn off?" }));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/push/unsubscribe", { method: "POST", body: { endpoint: "https://push/phone" } }));
    expect(sub.unsubscribe).not.toHaveBeenCalled();
  });
});
