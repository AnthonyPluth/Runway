// @vitest-environment jsdom
import { render, screen, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

import { api } from "$lib/api";
import CardList from "./CardList.svelte";
import { benefit, calls, card, churning } from "./fixtures";

beforeEach(() => { vi.mocked(api).mockReset(); vi.mocked(api).mockResolvedValue({ ok: true } as never); });

describe("the card list", () => {
  const setup = (over = {}) => render(CardList, { cards: [card(over)], d: churning(), showOwner: false, onedit: vi.fn(), onchanged: vi.fn() });

  it("keeps the bank, 5/24 and the plan out of the row until you open it", async () => {
    setup({ plan: "close", plan_active: true, plan_date: "2026-10-20" });
    expect(screen.queryByText(/5\/24/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^Plan:/)).not.toBeInTheDocument();
    expect(screen.getByText("Plan")).toBeInTheDocument();          // a plan that's due still shows, as a badge
    await userEvent.click(screen.getByRole("button", { name: "Venture X details" }));
    expect(screen.getByTestId("card-details-1")).toHaveTextContent(/opened Mar 2025 · counts toward 5\/24 until/);
    await userEvent.click(screen.getByRole("button", { name: "Mark the plan for Venture X done" }));
    expect(calls(/plan/).length).toBeGreaterThan(0);
  });

  // d.today is 2026-09-30 in the fixtures.
  const active = (deadline: string, over = {}) => ({ bonus: 60000, currency: "c1", bonus_spend: 4000, spent: 1000, bonus_state: "active" as const, deadline, ...over });
  const bar = () => screen.getByRole("progressbar").firstElementChild as HTMLElement;

  it("colors the annual fee's due date by how soon it is, with the distance as its tooltip", () => {
    for (const [due, cls, label] of [["2026-10-05", "text-loss", "in 5 days"], ["2026-10-25", "text-warning", "in 25 days"], ["2027-03-14", "text-muted-foreground", "in 165 days"]] as const) {
      const { unmount } = render(CardList, { cards: [card({ fee_due: due })], d: churning(), showOwner: false, onedit: vi.fn(), onchanged: vi.fn() });
      const el = screen.getByText(/^due /);
      expect(el).toHaveClass(cls);
      expect(el).toHaveAttribute("title", label);
      unmount();
    }
  });

  it("doesn't color a closed card's fee date", () => {
    setup({ status: "closed", fee_due: "2026-10-05" });
    expect(screen.getByText(/^due /)).toHaveClass("text-muted-foreground");
  });

  it("takes the bonus deadline's tone on the date and the spending bar, and says the daily need in the tooltip only", () => {
    setup(active("2026-10-07"));   // 7 days: red; $3,000 to go is $429 a day, rounded up
    expect(screen.getByText(/^by Oct/)).toHaveClass("text-loss");
    expect(bar()).toHaveClass("bg-loss");
    const tip = screen.getByText(/of \$4,000/).closest("div[title]")!;
    expect(tip).toHaveAttribute("title", "in 7 days · $429/day needed");
    expect(screen.queryByText(/\/day needed/)).toBeNull();   // not visible text
  });

  it("is amber within 30 days and the usual color after", () => {
    const { unmount } = render(CardList, { cards: [card(active("2026-10-25"))], d: churning(), showOwner: false, onedit: vi.fn(), onchanged: vi.fn() });
    expect(bar()).toHaveClass("bg-warning");
    expect(screen.getByText(/^by Oct/)).toHaveClass("text-warning");
    unmount();
    render(CardList, { cards: [card(active("2026-12-25"))], d: churning(), showOwner: false, onedit: vi.fn(), onchanged: vi.fn() });
    expect(bar().className).toContain("--nw-1");
    expect(screen.getByText(/^by Dec/)).toHaveClass("text-muted-foreground");
  });

  it("shows a finished spend as green whatever the date, and a missed bonus as a loss", () => {
    const { unmount } = render(CardList, { cards: [card(active("2026-10-05", { spent: 4000 }))], d: churning(), showOwner: false, onedit: vi.fn(), onchanged: vi.fn() });
    expect(bar()).toHaveClass("bg-good");
    unmount();
    setup(active("2026-09-01", { bonus_state: "missed" }));
    expect(bar()).toHaveClass("bg-loss/70");
    expect(screen.getByText("Missed")).toHaveClass("text-loss");
  });

  it("puts the net fee in the row, as one muted figure, and not in the details", async () => {
    setup({ annual_fee: 395, benefits: [benefit()], benefits_value: 450, net_fee: -55 });
    expect(screen.getByText("net −$55 after credits")).toHaveClass("text-muted-foreground");
    await userEvent.click(screen.getByRole("button", { name: "Venture X details" }));
    expect(screen.getByTestId("card-details-1")).not.toHaveTextContent(/net fee|Benefits \$/);
  });

  it("shows no net figure for a card without benefits", () => {
    setup({ annual_fee: 395 });
    expect(screen.queryByText(/after credits/)).toBeNull();
  });

  it("tells a status from an attribute: statuses are filled with their tone, AU and Business are the same neutral outline", () => {
    setup({ status: "closed", authorized_user: 1, business: 1, plan: "close", plan_active: true, plan_date: "2026-10-20", closed_on: "2026-08-01" });
    const row = screen.getByText("Venture X").closest("li")!;
    const chip = (t: string) => within(row).getByText(t);
    expect(chip("Closed")).toHaveAttribute("data-chip", "neutral");
    expect(chip("AU")).toHaveAttribute("data-chip", "attribute");
    expect(chip("Business")).toHaveAttribute("data-chip", "attribute");
    expect(chip("AU").className).toBe(chip("Business").className);
    expect(chip("Plan")).toHaveAttribute("data-chip", "warn");
    expect(chip("Plan").className).toContain("bg-warning/15");
    expect(chip("Closed").className).not.toBe(chip("AU").className);
  });
});
