// @vitest-environment jsdom
import { render, screen } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn().mockResolvedValue({}), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

import { api } from "$lib/api";
import { app } from "$lib/app.svelte";
import { categories } from "$lib/categories.svelte";
import type { ForecastEvent } from "$lib/types";
import { toast } from "svelte-sonner";
import { category } from "../../../test/fixtures";
import EventsList from "./EventsList.svelte";

type Ev = ForecastEvent & { late_from?: string | null };
const ev = (extra: Partial<Ev> = {}): Ev => ({ date: "2026-03-15", name: "Rent", amount: -1500, kind: "recurring", key: "k1", balance_after: 900, ...extra });
// `events` is also a Testing Library mount option, so props go under `props`.
const show = (events: Ev[], extra: Record<string, unknown> = {}) => render(EventsList, { props: { events, ...extra } });

beforeEach(() => {
  app.state = null;
  categories.list = [category("Housing", { icon: "🏠" }), category("Credit Card Payment", { icon: "💳" })];
  vi.mocked(api).mockClear();
});

describe("EventsList", () => {
  it("points to Recurring when nothing is scheduled", () => {
    show([]);
    expect(screen.getByRole("link", { name: "Recurring" })).toHaveAttribute("href", "#recurring");
  });

  it("shows each item's name, date, amount and the balance after it", () => {
    show([ev()]);
    expect(screen.getByText("Rent")).toBeInTheDocument();
    expect(screen.getByText(/Sun, Mar 15/)).toBeInTheDocument();
    expect(screen.getByText(/balance \$900\.00/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "−$1,500.00" })).toBeInTheDocument();
  });

  it("marks a balance that goes negative", () => {
    show([ev({ balance_after: -20 })]);
    expect(screen.getByText("balance -$20.00")).toHaveClass("text-destructive");
  });

  it("shows income as a green plus amount", () => {
    show([ev({ name: "Pay", amount: 3000, kind: "recurring" })]);
    expect(screen.getByRole("button", { name: "+$3,000.00" })).toHaveClass("text-emerald-500");
  });

  describe("icon", () => {
    it("prefers the merchant's logo", () => {
      const { container } = show([ev({ logo: "/logos/landlord.png", category: "Housing" })]);
      expect(container.querySelector("img")).toHaveAttribute("src", "/logos/landlord.png");
    });

    it("uses the bank's logo for a card payment", () => {
      app.state = { connected: true, brands: { card1: { institution: "Chase", src: "/api/merchants/brand%3Achase/logo" } } };
      const { container } = show([ev({ kind: "card", card_id: "card1", name: "Chase Sapphire", key: undefined })]);
      const img = container.querySelector("img")!;
      expect(img).toHaveAttribute("src", "/api/merchants/brand%3Achase/logo");
      expect(img).toHaveAttribute("title", "Chase");
    });

    it("falls back to the category's emoji", () => {
      const { container } = show([ev({ category: "Housing" })]);
      expect(container.querySelector("img")).toBeNull();
      expect(container.textContent).toContain("🏠");
    });

    it("shows the Credit Card Payment emoji for a card with no bank logo", () => {
      const { container } = show([ev({ kind: "card", card_id: "x", key: undefined })]);
      expect(container.textContent).toContain("💳");
    });
  });

  describe("badges", () => {
    it("flags estimates, late items and edited amounts", () => {
      show([ev({ estimated: true, late_from: "2026-03-01", overridden: true, original_amount: -1400 })]);
      expect(screen.getByText("estimate")).toBeInTheDocument();
      expect(screen.getByText("late")).toHaveAttribute("title", "Was due 2026-03-01 and hasn't shown up yet");
      expect(screen.getByText("edited")).toHaveAttribute("title", "Usually -$1,400.00");
    });

    it("explains a card estimate differently from a recurring one", () => {
      show([ev({ kind: "card", estimated: true, key: undefined })]);
      expect(screen.getByText("estimate").title).toMatch(/Statement hasn't closed yet/);
    });

    it("marks recurring items with ↻", () => {
      show([ev()]);
      expect(screen.getByLabelText("Recurring item")).toBeInTheDocument();
    });
  });

  it("adds the account when several accounts' items are shown together", () => {
    show([ev({ account: "Checking" })], { accounts: true });
    expect(screen.getByText(/· Checking/)).toBeInTheDocument();
  });

  describe("limit", () => {
    const many = Array.from({ length: 10 }, (_, i) => ev({ name: `Bill ${i}`, key: `k${i}` }));

    it("shows the first few, then all of them on request", async () => {
      show(many, { limit: 3 });
      expect(screen.getAllByText(/^Bill \d$/)).toHaveLength(3);
      await userEvent.click(screen.getByRole("button", { name: /Show all 10/ }));
      expect(screen.getAllByText(/^Bill \d$/)).toHaveLength(10);
      expect(screen.queryByRole("button", { name: /Show all/ })).not.toBeInTheDocument();
    });

    it("collapses again with Show fewer", async () => {
      show(many, { limit: 3 });
      expect(screen.queryByRole("button", { name: "Show fewer" })).not.toBeInTheDocument();
      await userEvent.click(screen.getByRole("button", { name: /Show all 10/ }));
      await userEvent.click(screen.getByRole("button", { name: "Show fewer" }));
      expect(screen.getAllByText(/^Bill \d$/)).toHaveLength(3);
      expect(screen.getByRole("button", { name: /Show all 10/ })).toBeInTheDocument();
    });

    it("has nothing to collapse when everything fits", () => {
      show(many.slice(0, 2), { limit: 3 });
      expect(screen.queryByRole("button", { name: "Show fewer" })).not.toBeInTheDocument();
    });
  });

  describe("changing one occurrence", () => {
    it("overrides just that date, keeping the sign of the original", async () => {
      show([ev()]);
      await userEvent.click(screen.getByRole("button", { name: "−$1,500.00" }));
      const input = screen.getByRole("spinbutton", { name: "Amount" });
      await userEvent.clear(input);
      await userEvent.type(input, "1400{Enter}");
      expect(api).toHaveBeenCalledWith("/api/overrides", { method: "POST", body: { key: "k1", amount: -1400 } });
      expect(toast.success).toHaveBeenCalledWith("Updated for this date only");
    });

    it("keeps an income positive", async () => {
      show([ev({ amount: 3000 })]);
      await userEvent.click(screen.getByRole("button", { name: "+$3,000.00" }));
      const input = screen.getByRole("spinbutton");
      await userEvent.clear(input);
      await userEvent.type(input, "3100{Enter}");
      expect(api).toHaveBeenCalledWith("/api/overrides", { method: "POST", body: { key: "k1", amount: 3100 } });
    });

    it("shows the error if it can't be saved", async () => {
      vi.mocked(api).mockRejectedValueOnce(new Error("Nope"));
      show([ev()]);
      await userEvent.click(screen.getByRole("button", { name: "−$1,500.00" }));
      const input = screen.getByRole("spinbutton");
      await userEvent.clear(input);
      await userEvent.type(input, "1{Enter}");
      expect(toast.error).toHaveBeenCalledWith("Nope");
    });

    it("can't be edited when the item has no key (a card statement)", () => {
      show([ev({ key: undefined })]);
      expect(screen.queryByRole("button", { name: /1,500/ })).not.toBeInTheDocument();
      expect(screen.getByText("-$1,500.00")).toBeInTheDocument();
    });

    it("resets an edited amount to the usual one", async () => {
      show([ev({ overridden: true, original_amount: -1400 })]);
      await userEvent.click(screen.getByRole("button", { name: "reset" }));
      expect(api).toHaveBeenCalledWith("/api/overrides", { method: "DELETE", body: { key: "k1" } });
      expect(toast.success).toHaveBeenCalledWith("Back to the usual amount");
    });
  });
});
