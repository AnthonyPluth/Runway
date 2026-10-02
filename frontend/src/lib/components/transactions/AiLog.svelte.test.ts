// @vitest-environment jsdom
import { render, screen } from "@testing-library/svelte";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));

import { api } from "$lib/api";
import AiLog from "./AiLog.svelte";
import AiLogTable from "./AiLogTable.svelte";

const row = (extra = {}) => ({ id: 1, at: "2026-03-10 09:05:00", purpose: "review", model: "m/x", merchants: 8, answered: 5, ok: 1, seconds: 3, ...extra });
beforeEach(() => vi.mocked(api).mockReset());

describe("AiLog", () => {
  it("is one line: when the AI last ran and how many it suggested", async () => {
    vi.mocked(api).mockResolvedValue([row(), row({ id: 0, ok: 0 })]);
    render(AiLog);
    expect(await screen.findByText(/Last run Mar 10, 9:05 AM: 5 of 8 suggested/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("says a failed run failed, and shows nothing before the first one", async () => {
    vi.mocked(api).mockResolvedValue([row({ ok: 0, purpose: "sync" })]);
    const { unmount } = render(AiLog);
    expect(await screen.findByText(/Last automatic run .*: failed/)).toBeInTheDocument();
    unmount();
    vi.mocked(api).mockResolvedValue([]);
    const { container } = render(AiLog);
    await vi.waitFor(() => expect(api).toHaveBeenCalledTimes(2));
    expect(container.textContent?.trim()).toBe("");
  });
});

describe("AiLogTable", () => {
  it("lists every request", async () => {
    vi.mocked(api).mockResolvedValue([row({ message: "boom", ok: 0 }), row({ id: 2 })]);
    render(AiLogTable);
    expect(await screen.findByRole("table", { name: "AI requests" })).toBeInTheDocument();
    expect(screen.getAllByRole("row")).toHaveLength(3);
    expect(screen.getByText("5/8")).toBeInTheDocument();
    expect(screen.getByText(/boom/)).toBeInTheDocument();
  });
});
