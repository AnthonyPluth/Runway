// @vitest-environment jsdom
import { render, screen } from "@testing-library/svelte";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));

import { api } from "$lib/api";
import { app } from "$lib/app.svelte";
import Reports from "./Reports.svelte";

beforeEach(() => {
  vi.mocked(api).mockReset();
  vi.mocked(api).mockResolvedValue({ month: "2026-03", income: [], spending: [], total_in: 0, total_out: 0, net: 0 } as never);
});

describe("Reports page", () => {
  it("asks you to connect a bank first, on every tab, keeping the heading and tabs", () => {
    app.state = { connected: false };
    for (const sub of ["", "trends", "merchants", "income", "breakdown"]) {
      const { unmount } = render(Reports, { sub });
      expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Reports");
      expect(screen.getByRole("navigation", { name: "Reports" })).toBeInTheDocument();
      expect(screen.getByText("Connect a bank to see where your money goes")).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "Connect a bank" })).toHaveAttribute("href", "#setup/connections");
      unmount();
    }
    expect(api).not.toHaveBeenCalled();
  });

  it("shows the report once a bank is connected", async () => {
    app.state = { connected: true };
    render(Reports);
    expect(await screen.findByText(/No transactions in/)).toBeInTheDocument();
    expect(screen.queryByText("Connect a bank to see where your money goes")).not.toBeInTheDocument();
  });
});
