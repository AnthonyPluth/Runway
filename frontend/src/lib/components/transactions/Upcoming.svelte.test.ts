// @vitest-environment jsdom
import { render, screen } from "@testing-library/svelte";
import { app } from "$lib/app.svelte";
import { describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn().mockResolvedValue({}), newPage: vi.fn() }));

import Upcoming from "./Upcoming.svelte";
import type { UpcomingEvent } from "./types";

const events = (n: number): UpcomingEvent[] => Array.from({ length: n }, (_, i) => ({
  date: `2026-03-${10 + i}`, name: `Bill ${i + 1}`, amount: -10, kind: "recurring", key: `k${i}`, balance_after: 100, account_id: "a1", account: "Checking",
}) as UpcomingEvent);

const width = (wide: boolean) => vi.spyOn(window, "matchMedia").mockImplementation((q) => ({
  matches: wide, media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, onchange: null, dispatchEvent: () => false,
}) as MediaQueryList);

describe("Upcoming", () => {
  it("shows 4 items on a narrow screen and offers the rest", () => {
    width(false);
    render(Upcoming, { props: { onchanged: vi.fn(), events: events(6) } });
    expect(screen.getByText("Bill 4")).toBeInTheDocument();
    expect(screen.queryByText("Bill 5")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Show all 6/ })).toBeInTheDocument();
  });

  it("collapses to the first 3 on desktop", () => {
    width(true);
    render(Upcoming, { props: { onchanged: vi.fn(), events: events(6) } });
    expect(screen.getByText("Bill 3")).toBeInTheDocument();
    expect(screen.queryByText("Bill 4")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Show all 6/ })).toBeInTheDocument();
  });

  it("marks each item with its account's bank, unless the list is filtered to one", () => {
    width(false);
    app.state = { connected: true, brands: { a1: { institution: "Chase", initial: "C" } } } as typeof app.state;
    const { unmount, container } = render(Upcoming, { props: { onchanged: vi.fn(), events: events(1) } });
    expect(container.querySelector("[data-account-badge]")).toHaveAttribute("title", "Checking");
    expect(screen.getAllByText(/^Checking/).length).toBe(1);
    unmount();
    const one = render(Upcoming, { props: { onchanged: vi.fn(), events: events(1), oneAccount: true } });
    expect(one.container.querySelector("[data-account-badge]")).toBeNull();
    expect(screen.queryByText(/Checking/)).not.toBeInTheDocument();
    expect(screen.getByText("projected balance $100.00")).toBeInTheDocument();
    app.state = null;
  });
});
