// @vitest-environment jsdom
import { render, screen, within } from "@testing-library/svelte";
import { describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

import BankList from "./BankList.svelte";
import { bankBonus, churning } from "./fixtures";
import type { BankBonus } from "./types";

const setup = (over: Partial<BankBonus> = {}, d = churning()) => render(BankList, { bonuses: [bankBonus({ progress: { dd_total: 100, dd_count: 0, debits: 0, balance: null, balance_ok: null, met: false, source: "account" }, ...over })], d, showOwner: false, onedit: vi.fn() });
const bar = () => screen.getByRole("progressbar").firstElementChild as HTMLElement;

describe("the bank bonus list", () => {
  it("colors the due date red within 7 days, amber within 30, muted after, and says how far in its tooltip", () => {
    for (const [due, cls, label] of [["2026-10-04", "text-loss", "in 4 days"], ["2026-10-20", "text-warning", "in 20 days"], ["2026-12-05", "text-foreground", "in 66 days"]] as const) {
      const { unmount } = setup({ due });
      expect(screen.getByText(/^Due/).querySelector("span")).toHaveClass(cls);
      expect(screen.getByText(/^Due/)).toHaveAttribute("title", label);
      unmount();
    }
  });

  it("takes the due date's tone on the deposits bar", () => {
    const { unmount } = setup({ due: "2026-10-04" });
    expect(bar()).toHaveClass("bg-loss");
    unmount();
    setup({ due: "2026-10-20" });
    expect(bar()).toHaveClass("bg-warning");
  });

  it("fills a status with its tone: met and received are good, missed is bad, closed is neutral", () => {
    const tones = { received: "good", met: "good", missed: "bad", closed: "neutral" } as const;
    for (const state of ["received", "met", "missed", "closed"] as const) {
      const { unmount } = setup({ state, received_on: state === "received" ? "2026-03-01" : null, due: "2026-04-01" });
      const row = screen.getByRole("listitem");
      const chip = row.querySelector("[data-chip]") as HTMLElement;
      expect(chip).toHaveAttribute("data-chip", tones[state]);
      unmount();
    }
  });

  it("says Requirements met for a bonus waiting to post", () => {
    setup({ state: "met" });
    expect(within(screen.getByRole("listitem")).getByText("Requirements met")).toBeInTheDocument();
  });

  it("shows the opened year when it isn't the server's year", () => {
    setup({ opened_on: "2025-11-02" });
    expect(screen.getByText(/opened Nov.2, 2025/)).toBeInTheDocument();
  });
});
