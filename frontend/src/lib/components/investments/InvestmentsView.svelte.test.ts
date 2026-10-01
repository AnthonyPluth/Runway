// @vitest-environment jsdom
import { render, screen, within } from "@testing-library/svelte";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));

import { api } from "$lib/api";
import InvestmentsView from "./InvestmentsView.svelte";

beforeEach(() => { vi.mocked(api).mockReset(); });

describe("InvestmentsView", () => {
  it("with no investment accounts, says what the tab shows and links to Connections", async () => {
    vi.mocked(api).mockResolvedValue({ inv_accounts: 0, items: [] } as never);
    render(InvestmentsView);
    const block = await screen.findByTestId("getting-started");
    expect(within(block).getByText(/holdings, allocation, dividends and activity/)).toBeInTheDocument();
    expect(within(block).getByRole("link", { name: "Connect an investment account" })).toHaveAttribute("href", "#setup/connections");
    expect(screen.queryByText("No investment accounts yet")).toBeNull();
  });
});
