// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { toast } from "svelte-sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn(), session: {} }));
vi.mock("svelte-sonner", () => ({
  toast: Object.assign(vi.fn(), {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
  }),
}));

import { api } from "$lib/api";
import { app, route } from "$lib/app.svelte";
import type { AppState } from "$lib/types";
import MobileNav from "./MobileNav.svelte";
import Sidebar from "./Sidebar.svelte";
import SyncButton from "./SyncButton.svelte";

class ApiError extends Error {
  constructor(
    m: string,
    readonly status: number,
  ) {
    super(m);
  }
}
const state = (extra: Partial<AppState> = {}): AppState => ({
  connected: true,
  last_sync_ok: new Date(Date.now() - 36e5).toISOString(),
  ...extra,
});
beforeEach(() => {
  vi.mocked(api).mockReset();
  for (const f of [toast, toast.success, toast.error, toast.warning])
    vi.mocked(f).mockReset();
  app.state = state();
  app.version = 0;
  route.page = "overview";
  route.sub = "";
});

describe("SyncButton", () => {
  it("syncs with a POST, shows Syncing… meanwhile, then toasts the result and refreshes", async () => {
    let finish!: (v: unknown) => void;
    vi.mocked(api).mockImplementation((path: string) =>
      path === "/api/sync"
        ? new Promise((r) => {
            finish = r;
          })
        : Promise.resolve(state({ last_sync_ok: "2026-10-01T07:02:00" })),
    );
    render(Sidebar);
    const button = screen.getByRole("button", { name: "Sync now" });
    await userEvent.click(button);
    expect(api).toHaveBeenCalledWith("/api/sync", { method: "POST" });
    expect(screen.getByRole("status")).toHaveTextContent("Syncing…");
    expect(button).toBeDisabled();
    finish({ new: 3, categorized: {}, bank_messages: [] });
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith("Synced · 3 new transactions"),
    );
    expect(api).toHaveBeenCalledWith("/api/state", expect.anything());
    expect(app.version).toBe(1);
    expect(app.state?.last_sync_ok).toBe("2026-10-01T07:02:00");
    expect(screen.getByRole("button", { name: "Sync now" })).toBeEnabled();
  });

  it("says one new transaction in the singular, and shows what a bank said", async () => {
    vi.mocked(api).mockImplementation(async (path: string) =>
      path === "/api/sync"
        ? { new: 1, bank_messages: ["Chase: log in again"] }
        : state(),
    );
    render(SyncButton);
    await userEvent.click(screen.getByRole("button", { name: "Sync now" }));
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith("Synced · 1 new transaction"),
    );
    expect(toast.warning).toHaveBeenCalledWith("Chase: log in again");
  });

  it("toasts the error when the sync fails, and goes back to idle", async () => {
    vi.mocked(api).mockImplementation(async (path: string) => {
      if (path === "/api/sync") throw new ApiError("SimpleFIN said no", 502);
      return state({ last_log: { ok: false, message: "SimpleFIN said no" } });
    });
    render(Sidebar);
    await userEvent.click(screen.getByRole("button", { name: "Sync now" }));
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith("SimpleFIN said no"),
    );
    expect(toast.success).not.toHaveBeenCalled();
    expect(app.version).toBe(0);
    expect(screen.getByRole("button", { name: "Sync now" })).toBeEnabled();
    expect(screen.getByRole("status")).toHaveTextContent("Last sync failed");
  });

  it("doesn't fail when a sync is already running: it says so, keeps watching, and says if that sync fails", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      let running = true;
      vi.mocked(api).mockImplementation(async (path: string) => {
        if (path === "/api/sync")
          throw new ApiError("A sync is already running.", 409);
        return running
          ? state({
              syncing: true,
              last_log: { at: "2026-10-01T06:00:00", ok: true, message: "" },
            })
          : state({
              syncing: false,
              last_log: {
                at: "2026-10-01T07:00:00",
                ok: false,
                message: "SimpleFIN is down",
              },
            });
      });
      app.state = state({
        last_log: { at: "2026-10-01T06:00:00", ok: true, message: "" },
      });
      render(SyncButton);
      await userEvent.click(screen.getByRole("button", { name: "Sync now" }));
      await waitFor(() =>
        expect(toast).toHaveBeenCalledWith(
          expect.stringContaining("already running"),
        ),
      );
      expect(toast.error).not.toHaveBeenCalled();
      expect(screen.getByRole("button", { name: "Sync now" })).toBeDisabled();
      running = false;
      await vi.advanceTimersByTimeAsync(3100);
      await waitFor(() =>
        expect(toast.error).toHaveBeenCalledWith("SimpleFIN is down"),
      );
    } finally {
      vi.useRealTimers();
    }
  });

  it("doesn't reload under a form you're typing in, and says the page is older", async () => {
    let finish!: (v: unknown) => void;
    vi.mocked(api).mockImplementation((path: string) =>
      path === "/api/sync"
        ? new Promise((r) => {
            finish = r;
          })
        : Promise.resolve(state()),
    );
    render(SyncButton);
    const input = document.body.appendChild(document.createElement("input"));
    await userEvent.click(screen.getByRole("button", { name: "Sync now" }));
    input.focus();
    finish({ new: 2, bank_messages: [] });
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(
        "Synced · 2 new transactions. Change page to see the new data.",
      ),
    );
    expect(app.version).toBe(0);
    input.remove();
  });

  it("keeps watching a sync a proxy gave up on (a 504) while the server carried on", async () => {
    vi.mocked(api).mockImplementation(async (path: string) => {
      if (path === "/api/sync")
        throw new ApiError("Couldn't reach Runway", 504);
      return state({ syncing: true });
    });
    render(SyncButton);
    await userEvent.click(screen.getByRole("button", { name: "Sync now" }));
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith("Couldn't reach Runway"),
    );
    expect(app.state?.syncing).toBe(true);
    expect(screen.getByRole("button", { name: "Sync now" })).toBeDisabled();
  });

  it("is disabled while the server is syncing (the daily sync, or a visit's)", () => {
    app.state = state({ syncing: true });
    render(SyncButton);
    expect(screen.getByRole("button", { name: "Sync now" })).toBeDisabled();
  });

  it("is hidden when no bank is connected", () => {
    app.state = state({ connected: false });
    render(Sidebar);
    expect(screen.queryByRole("button", { name: "Sync now" })).toBeNull();
  });

  it("is in the More sheet on a phone", async () => {
    render(MobileNav);
    expect(screen.queryByRole("button", { name: "Sync now" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "More" }));
    vi.mocked(api).mockImplementation(async (path: string) =>
      path === "/api/sync" ? { new: 0, bank_messages: [] } : state(),
    );
    await userEvent.click(screen.getByRole("button", { name: "Sync now" }));
    expect(api).toHaveBeenCalledWith("/api/sync", { method: "POST" });
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith("Synced · 0 new transactions"),
    );
  });
});
