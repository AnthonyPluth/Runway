// @vitest-environment jsdom
import { render, screen } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

import { api } from "$lib/api";
import CardList from "./CardList.svelte";
import { calls, card, churning } from "./fixtures";

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
});
