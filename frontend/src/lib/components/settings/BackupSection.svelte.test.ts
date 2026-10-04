// @vitest-environment jsdom
import { render, screen, waitFor, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));
vi.mock("$lib/app.svelte", () => ({ app: { state: { database: "sqlite", last_backup: null } }, reload: vi.fn(), refreshState: vi.fn(async () => {}) }));

import { app, refreshState, reload } from "$lib/app.svelte";
import { cleanup } from "@testing-library/svelte";
import { toast } from "svelte-sonner";
import BackupSection from "./BackupSection.svelte";

const counts = (accounts: number, transactions: number) => ({ accounts, transactions, recurring: 4, budgets: 1, total: 99 });
const reply = (status: number, body: unknown) => ({ ok: status < 400, status, json: async () => body }) as Response;
const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset(); vi.mocked(reload).mockClear(); vi.mocked(toast.success).mockClear(); vi.mocked(toast.error).mockClear();
  vi.stubGlobal("fetch", fetchMock);
});

const choose = async (name = "runway-backup.json.gz") => {
  const input = screen.getByLabelText("Restore from a backup");
  await userEvent.upload(input, new File([new Uint8Array([0x1f, 0x8b])], name, { type: "application/gzip" }));
};

describe("Settings → Data: download", () => {
  it("says the file holds the bank keys, and when one was last downloaded", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout"] });
    try {
      app.state!.last_backup = `${new Date().getFullYear()}-09-28T12:15:00-04:00`;
      render(BackupSection);
      expect(screen.getByText("Your bank keys are in it, encrypted. Keep the file private.")).toBeInTheDocument();
      expect(screen.getByText("Last backup: Sep 28")).toBeInTheDocument();
      const link = screen.getByRole("link", { name: "Download a backup" });
      expect(link).toHaveAttribute("href", "/api/backup");
      link.addEventListener("click", (e) => e.preventDefault());   // jsdom can't download
      link.click();
      vi.mocked(refreshState).mockClear();
      vi.advanceTimersByTime(3000);
      expect(refreshState).toHaveBeenCalled();                     // to pick up the new "Last backup"
      cleanup();
      app.state!.last_backup = "2025-01-05T08:00:00-05:00";
      render(BackupSection);
      expect(screen.getByText("Last backup: Jan 5, 2025")).toBeInTheDocument();
      cleanup();
      app.state!.last_backup = null;
      render(BackupSection);
      expect(screen.queryByText(/Last backup/)).not.toBeInTheDocument();
    } finally { vi.useRealTimers(); app.state!.last_backup = null; }
  });
});

describe("Settings → Data: restore", () => {
  it("shows what the backup holds, and restores only once RESTORE is typed", async () => {
    fetchMock.mockImplementation(async (path: string) => path === "/api/backup/inspect"
      ? reply(200, { created: "2026-09-01T09:30:00", source: "postgres", version: 1, counts: counts(3, 1234), current: counts(5, 2500), database: "sqlite" })
      : reply(200, { ok: true, created: "2026-09-01T09:30:00", safety_copy: "/data/runway-before-restore-2026-09-30-101500.json.gz", unreadable_secrets: ["plaid:x"] }));
    render(BackupSection);
    const user = userEvent.setup();
    const open = screen.getByRole("button", { name: "Restore…" });
    expect(open).toBeDisabled();                                       // nothing chosen yet
    await choose();
    expect(await screen.findByText("Backup from Sep 1, 2026, 9:30 AM (Postgres): 3 accounts, 1,234 transactions, 4 recurring items, 1 budget"))
      .toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith("/api/backup/inspect", expect.objectContaining({ method: "POST" }));
    expect(fetchMock).not.toHaveBeenCalledWith("/api/restore", expect.anything());
    await waitFor(() => expect(open).toBeEnabled());
    await user.click(open);

    const dialog = await screen.findByRole("dialog", { name: "Replace everything with this backup?" });
    expect(dialog).toHaveTextContent("This replaces everything in this Runway (currently 2,500 transactions) with the backup. It can’t be undone.");
    expect(dialog).toHaveTextContent("Runway first saves a copy of what’s here now in its data folder.");
    const go = within(dialog).getByRole("button", { name: "Restore" });
    expect(go).toBeDisabled();
    await user.type(within(dialog).getByLabelText(/Type RESTORE to confirm/), "RESTORE");
    await user.click(go);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(fetchMock).toHaveBeenCalledWith("/api/restore", expect.objectContaining({ method: "POST" }));
    expect(toast.success).toHaveBeenCalledWith("Restored");
    expect(reload).toHaveBeenCalled();

    // What's worth keeping stays under Restore, through the page redrawing, until it's dismissed.
    const note = () => screen.getByRole("alert");
    expect(note()).toHaveTextContent("Restored the backup from Sep 1, 2026, 9:30 AM.");
    expect(note()).toHaveTextContent("A copy of what was here before is at /data/runway-before-restore-2026-09-30-101500.json.gz.");
    expect(note()).toHaveTextContent("1 saved key or connection can’t be read with this Runway’s secret key. Set the key the backup was made with as RUNWAY_SECRET_KEY_OLD");
    cleanup();
    render(BackupSection);
    expect(note()).toHaveTextContent("Restored the backup from Sep 1, 2026");
    await user.click(within(note()).getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    cleanup();
    render(BackupSection);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("says when a backup from an older version may have older data not brought up to date", async () => {
    const warning = "This backup is from an older version of Runway that didn’t record its database version, so some of its older data may not have been brought up to date. Check your accounts, payees and budgets.";
    fetchMock.mockImplementation(async (path: string) => path === "/api/backup/inspect"
      ? reply(200, { created: "2026-05-01T09:30:00", source: "sqlite", version: 1, revision: null, warning, counts: counts(3, 10), current: counts(0, 0), database: "sqlite" })
      : reply(200, { ok: true, created: "2026-05-01T09:30:00", safety_copy: null, unreadable_secrets: [], warning }));
    render(BackupSection);
    const user = userEvent.setup();
    await choose();
    expect(await screen.findByText(warning)).toBeInTheDocument();       // before you restore it
    await user.click(screen.getByRole("button", { name: "Restore…" }));
    const dialog = await screen.findByRole("dialog", { name: "Replace everything with this backup?" });
    expect(dialog).toHaveTextContent(warning);
    await user.type(within(dialog).getByLabelText(/Type RESTORE to confirm/), "RESTORE");
    await user.click(within(dialog).getByRole("button", { name: "Restore" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByRole("alert")).toHaveTextContent(warning);       // and after, until dismissed
    cleanup();
    render(BackupSection);
    await user.click(within(screen.getByRole("alert")).getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("says why a file can't be restored, and keeps Restore off", async () => {
    fetchMock.mockResolvedValue(reply(400, { error: "That file isn't a Runway backup." }));
    render(BackupSection);
    await choose("notes.json");
    expect(await screen.findByText("That file isn't a Runway backup.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Restore…" })).toBeDisabled();
  });

  it("says Runway can't be reached, as everywhere else, when the file can't be sent", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    render(BackupSection);
    await choose();
    expect(await screen.findByText("Can’t reach Runway. Check your connection and try again.")).toBeInTheDocument();
  });

  it("names a refusal that came with no words after the step it was on", async () => {
    fetchMock.mockResolvedValue(({ ok: false, status: 500, json: async () => { throw new Error("not json"); } }) as unknown as Response);
    render(BackupSection);
    await choose();
    expect(await screen.findByText("Couldn’t read that backup (500)")).toBeInTheDocument();
  });
});
