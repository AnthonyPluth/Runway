// @vitest-environment jsdom
import { render, screen, waitFor, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

import { api } from "$lib/api";
import { toast } from "svelte-sonner";
import BenefitForm from "./BenefitForm.svelte";
import Benefits from "./Benefits.svelte";
import CardBenefits from "./CardBenefits.svelte";
import { benefit, bodyOf, calls, card, churning } from "./fixtures";

beforeEach(() => {
  vi.mocked(api).mockReset();
  vi.mocked(api).mockResolvedValue({ ok: true } as never);
  vi.mocked(toast).mockReset();
  vi.mocked(toast.error).mockReset();
});

describe("benefits", () => {
  const setup = (over = {}) => {
    const onchanged = vi.fn();
    const c = card({ annual_fee: 395, benefits: [benefit(over)], benefits_value: 300, net_fee: 95 });
    render(CardBenefits, { card: c, d: churning(), onchanged });
    return onchanged;
  };

  it("says what's left this period and marks the rest used, with an undo", async () => {
    vi.mocked(api).mockResolvedValue({ id: 7 } as never);
    const onchanged = setup({ used: 100, remaining: 200, used_count: 1 });
    expect(screen.getByText(/\$100 of \$300 used/)).toBeInTheDocument();
    expect(screen.getByText(/Benefits \$300\/yr · net fee \$95/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Mark Travel credit used" }));
    await waitFor(() => expect(calls("/api/churning/benefits/1/use")).toHaveLength(1));
    expect(bodyOf(calls("/api/churning/benefits/1/use")[0])).toEqual({});   // no amount: the rest
    expect(onchanged).toHaveBeenCalled();
    // The toast's Undo removes that very use.
    const opts = vi.mocked(toast).mock.calls.at(-1)![1] as { action: { label: string; onClick: () => void } };
    expect(opts.action.label).toBe("Undo");
    opts.action.onClick();
    await waitFor(() => expect(bodyOf(calls("/api/churning/benefits/1/unuse")[0])).toEqual({ use_id: 7 }));
  });

  it("marks a partial amount used, and undoes the latest use from the button", async () => {
    vi.mocked(api).mockResolvedValue({ id: 8 } as never);
    setup({ used: 100, remaining: 200, used_count: 1, uses: [{ id: 3, amount_used: 100, used_on: "2026-09-01" }] });
    await userEvent.type(screen.getByLabelText("Amount of Travel credit used (blank: the rest)"), "50");
    await userEvent.click(screen.getByRole("button", { name: "Mark Travel credit used" }));
    await waitFor(() => expect(bodyOf(calls("/api/churning/benefits/1/use")[0])).toEqual({ amount: 50 }));
    await userEvent.click(screen.getByRole("button", { name: "Undo the last use of Travel credit" }));
    await waitFor(() => expect(bodyOf(calls("/api/churning/benefits/1/unuse")[0])).toEqual({}));
    expect(screen.getByText("History (1)")).toBeInTheDocument();
  });

  it("disables Mark used once a credit is all used, and adds a preset", async () => {
    vi.mocked(api).mockResolvedValue({ id: 9 } as never);
    setup({ used: 300, remaining: 0, used_count: 1 });
    expect(screen.getByRole("button", { name: "Mark Travel credit used" })).toBeDisabled();
    expect(screen.getByText(/All \$300 used/)).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Add a benefit to Venture X"), "lounge");
    await waitFor(() => expect(bodyOf(calls("/api/churning/cards/1/benefits")[0])).toEqual({ preset: "lounge" }));
  });
});

describe("benefits tab", () => {
  it("lists each lounge network as a perk with who gets in free, apart from the credits", () => {
    const vx = card({ benefits: [
      benefit({ id: 1, name: "Travel credit" }),
      benefit({ id: 2, name: "Priority Pass lounges", kind: "access", amount: null, used: null, remaining: null, value_per_year: 0, guests: 0 }),
      benefit({ id: 3, name: "Capital One Lounges", kind: "access", amount: null, used: null, remaining: null, value_per_year: 0, guests: 0, used_count: 2 }),
    ] });
    const csr = card({ id: 2, product: "Sapphire Reserve", benefits: [
      benefit({ id: 4, card_id: 2, name: "Priority Pass lounges", kind: "access", amount: null, used: null, remaining: null, value_per_year: 0, guests: 2 }),
    ] });
    render(Benefits, { cards: [vx, csr], showOwner: false, onchanged: vi.fn() });
    const perks = screen.getByRole("list", { name: "Lounges & perks" });
    const rows = within(perks).getAllByRole("listitem").map((li) => li.textContent!.replace(/\s+/g, " ").trim());
    expect(rows).toEqual([   // by card, then name; each network its own row
      "Priority Pass lounges Sapphire Reserve Cardholder + 2 guests —",
      "Capital One Lounges Venture X Cardholder only · used twice this period —",
      "Priority Pass lounges Venture X Cardholder only —",
    ]);
    expect(within(perks).queryByRole("button")).toBeNull();   // nothing to use up
    expect(within(screen.getByRole("list", { name: "Still to use" })).getByRole("button", { name: "Mark Travel credit on Venture X used" })).toBeInTheDocument();
  });

  it("asks how many guests a lounge lets in, and sends it", async () => {
    vi.mocked(api).mockResolvedValue({ id: 5 } as never);
    const onclose = vi.fn();
    render(BenefitForm, { card: { id: 1, product: "Venture X" }, d: churning(), b: null, onclose });
    expect(screen.queryByLabelText(/Guests free/)).toBeNull();   // a credit has no guests
    await userEvent.type(screen.getByLabelText("Benefit"), "Priority Pass lounges");
    await userEvent.selectOptions(screen.getByLabelText("Kind"), "access");
    await userEvent.type(screen.getByLabelText(/Guests free/), "2");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(bodyOf(calls("/api/churning/cards/1/benefits")[0])).toMatchObject({ name: "Priority Pass lounges", kind: "access", guests: 2 }));
    expect(onclose).toHaveBeenCalledWith(true);
  });
});
