// @vitest-environment jsdom
import { render, screen, waitFor, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));
vi.mock("$lib/app.svelte", () => ({ app: { state: { database: "sqlite" } }, reload: vi.fn(), refreshState: vi.fn(async () => {}) }));

import { reload } from "$lib/app.svelte";
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

describe("Settings → Advanced: restore", () => {
  it("shows what the backup holds, and restores only once RESTORE is typed", async () => {
    fetchMock.mockImplementation(async (path: string) => path === "/api/backup/inspect"
      ? reply(200, { created: "2026-09-01T09:30:00", source: "postgres", version: 1, counts: counts(3, 1234), current: counts(5, 2500), database: "sqlite" })
      : reply(200, { ok: true, created: "2026-09-01T09:30:00", safety_copy: "/data/runway-before-restore-2026-09-30-101500.json.gz" }));
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
    expect(toast.success).toHaveBeenCalledWith("Restored. A copy of what was here is at /data/runway-before-restore-2026-09-30-101500.json.gz");
    expect(reload).toHaveBeenCalled();
  });

  it("says why a file can't be restored, and keeps Restore off", async () => {
    fetchMock.mockResolvedValue(reply(400, { error: "That file isn't a Runway backup." }));
    render(BackupSection);
    await choose("notes.json");
    expect(await screen.findByText("That file isn't a Runway backup.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Restore…" })).toBeDisabled();
  });
});
