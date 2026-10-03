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
import { benefit, bodyOf, calls, card, churning } from "./fixtures";

beforeEach(() => {
  vi.mocked(api).mockReset();
  vi.mocked(api).mockResolvedValue({ ok: true } as never);
  vi.mocked(toast).mockReset();
  vi.mocked(toast.error).mockReset();
});

describe("benefits tab", () => {
  it("with no benefits, is one muted line: no tiles of zeros, no prose", () => {
    const { container } = render(Benefits, { cards: [card()], showOwner: false, onchanged: vi.fn() });
    expect(screen.getByText(/none yet\. Add them from a card’s Edit/)).toBeInTheDocument();
    expect(container.querySelector("dl")).toBeNull();
    expect(screen.queryByText(/Open Runway on a computer/)).toBeNull();
    expect(screen.queryByText(/Edit a card and add/)).toBeNull();
  });

  it("shows its totals as an unboxed strip once there are benefits", () => {
    const { container } = render(Benefits, { cards: [card({ benefits: [benefit()] })], showOwner: false, onchanged: vi.fn() });
    expect(container.querySelector("dl")).not.toBeNull();
    expect(screen.getByText("Worth a year", { selector: "dt span" })).toBeInTheDocument();
    expect(screen.queryByText("Money left, and the period ends soon.")).toBeNull();
  });

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
    await userEvent.type(screen.getByLabelText(/^Benefit/), "Priority Pass lounges");
    await userEvent.selectOptions(screen.getByLabelText("Kind"), "access");
    await userEvent.type(screen.getByLabelText(/Guests free/), "2");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(bodyOf(calls("/api/churning/cards/1/benefits")[0])).toMatchObject({ name: "Priority Pass lounges", kind: "access", guests: 2 }));
    expect(onclose).toHaveBeenCalledWith(true);
  });
});

describe("marking a benefit used on the board", () => {
  const board = (b = {}, cardOver = {}) => {
    const onchanged = vi.fn();
    render(Benefits, { cards: [card({ benefits: [benefit({ remaining: 120, used: 180, used_count: 1, ...b })], ...cardOver })], showOwner: false, onchanged });
    return onchanged;
  };

  it("offers the amount in a small popover, the rest of the credit by default", async () => {
    vi.mocked(api).mockResolvedValue({ id: 5 } as never);
    const onchanged = board();
    expect(screen.queryByLabelText("Amount of Travel credit used")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Mark Travel credit on Venture X used" }));
    const amount = screen.getByLabelText("Amount of Travel credit used");
    expect(amount).toHaveValue(120);   // what's left
    await userEvent.click(screen.getByRole("button", { name: "Mark used" }));
    await waitFor(() => expect(calls("/api/churning/benefits/1/use")).toHaveLength(1));
    expect(bodyOf(calls("/api/churning/benefits/1/use")[0])).toEqual({});   // unchanged: the rest
    expect(onchanged).toHaveBeenCalled();
    expect(screen.queryByLabelText("Amount of Travel credit used")).toBeNull();   // the popover closes
  });

  it("sends the amount you typed instead", async () => {
    vi.mocked(api).mockResolvedValue({ id: 5 } as never);
    board();
    await userEvent.click(screen.getByRole("button", { name: "Mark Travel credit on Venture X used" }));
    const amount = screen.getByLabelText("Amount of Travel credit used");
    await userEvent.clear(amount);
    await userEvent.type(amount, "40{Enter}");
    await waitFor(() => expect(bodyOf(calls("/api/churning/benefits/1/use")[0])).toEqual({ amount: 40 }));
    expect(vi.mocked(toast).mock.calls.at(-1)![0]).toBe("Marked Travel credit used ($40.00)");
  });

  it("closes the popover without marking anything on Escape or a click elsewhere", async () => {
    board();
    await userEvent.click(screen.getByRole("button", { name: "Mark Travel credit on Venture X used" }));
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByLabelText("Amount of Travel credit used")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Mark Travel credit on Venture X used" }));
    await userEvent.click(document.body);
    expect(screen.queryByLabelText("Amount of Travel credit used")).toBeNull();
    expect(calls(/use$/)).toHaveLength(0);
  });

  it("marks a one-off used with one click, no amount to ask", async () => {
    board({ kind: "other", amount: null, remaining: null, used: null, used_count: 0, name: "Free night" });
    await userEvent.click(screen.getByRole("button", { name: "Mark Free night on Venture X used" }));
    await waitFor(() => expect(calls("/api/churning/benefits/1/use")).toHaveLength(1));
    expect(screen.queryByLabelText("Amount of Free night used")).toBeNull();
  });

  it("gives a used-up credit an Undo that takes the use off, and says how to put it back", async () => {
    const onchanged = board({ remaining: 0, used: 300, used_count: 1, uses: [{ id: 3, amount_used: 300, used_on: "2026-09-02" }] });
    const used = screen.getByRole("list", { name: "Used this period" });
    expect(within(used).queryByRole("button", { name: /Mark/ })).toBeNull();
    await userEvent.click(within(used).getByRole("button", { name: "Undo the use of Travel credit on Venture X" }));
    await waitFor(() => expect(bodyOf(calls("/api/churning/benefits/1/unuse")[0])).toEqual({ use_id: 3 }));
    expect(onchanged).toHaveBeenCalled();
    const [msg, opts] = vi.mocked(toast).mock.calls.at(-1)! as [string, { action: { label: string; onClick: () => void } }];
    expect(msg).toBe("Took the use off Travel credit");
    opts.action.onClick();
    await waitFor(() => expect(bodyOf(calls("/api/churning/benefits/1/use")[0])).toEqual({ used_on: "2026-09-02", amount: 300 }));
  });

  it("shows how much of a credit is used in the same thin bar as the card's own list", () => {
    board();
    const bar = screen.getByRole("progressbar", { name: "Travel credit on Venture X used this period" });
    expect(bar).toHaveAttribute("aria-valuenow", "180");
    expect(bar).toHaveAttribute("aria-valuemax", "300");
    expect(bar.firstElementChild).toHaveStyle({ width: "60%" });
    expect(bar.className).toContain("h-1.5");
  });

  it("says where benefits come from when there are none", () => {
    render(Benefits, { cards: [card()], showOwner: false, onchanged: vi.fn() });
    expect(screen.getByText(/Add them from a card’s Edit/)).toBeInTheDocument();
  });
});
