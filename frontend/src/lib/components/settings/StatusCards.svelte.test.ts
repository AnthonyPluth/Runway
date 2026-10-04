// @vitest-environment jsdom
import { fireEvent, render, screen } from "@testing-library/svelte";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", async (orig) => ({ ...(await orig()), api: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));
vi.mock("$lib/app.svelte", () => ({ app: { state: { logodev_configured: true, finnhub_configured: true }, version: 0 }, refreshState: vi.fn() }));

import { api } from "$lib/api";
import FinnhubCard from "./FinnhubCard.svelte";
import LogoDevCard from "./LogoDevCard.svelte";

beforeEach(() => { vi.mocked(api).mockReset(); });

const logos = { plaid: 4, logodev: 6, unknown: 0, waiting: 0, last_error: null, last_error_name: null, searchable: false, fetching: false };
const finnhub = { configured: true, active: true, connected: true, symbols: 3, limit: 50, error: null };

describe("the Logo.dev card's status", () => {
  it("says it couldn't load, rather than showing nothing, and loads on Retry", async () => {
    vi.mocked(api).mockRejectedValueOnce(new Error("Can’t reach Runway. Check your connection and try again."));
    render(LogoDevCard);
    expect(await screen.findByText(/Couldn’t load the logo status: Can’t reach Runway/)).toBeInTheDocument();
    expect(screen.queryByText(/merchants have a logo/)).toBeNull();
    vi.mocked(api).mockResolvedValueOnce(logos as never);
    await fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText(/merchants have a logo/)).toBeInTheDocument();
    expect(screen.queryByText(/Couldn’t load/)).toBeNull();
  });
});

describe("the Finnhub card's status", () => {
  it("takes the old status down when a refresh fails, instead of leaving it looking current", async () => {
    vi.mocked(api).mockResolvedValueOnce(finnhub as never);
    render(FinnhubCard);
    expect(await screen.findByText("Connected.")).toBeInTheDocument();
    vi.mocked(api).mockRejectedValueOnce(new Error("Runway is restarting or unreachable. Try again in a moment."));
    await fireEvent.click(screen.getByRole("button", { name: "Refresh status" }));
    expect(await screen.findByText(/Couldn’t load the connection status: Runway is restarting/)).toBeInTheDocument();
    expect(screen.queryByText("Connected.")).toBeNull();
    vi.mocked(api).mockResolvedValueOnce({ ...finnhub, connected: false, symbols: 0 } as never);
    await fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText(/Not connected right now/)).toBeInTheDocument();
  });
});
