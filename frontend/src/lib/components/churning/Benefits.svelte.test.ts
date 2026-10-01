// @vitest-environment jsdom
import { render, screen, waitFor, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

import { api } from "$lib/api";
import BenefitForm from "./BenefitForm.svelte";
import Benefits from "./Benefits.svelte";
import { benefit, bodyOf, calls, card, churning } from "./fixtures";

beforeEach(() => {
  vi.mocked(api).mockReset();
  vi.mocked(api).mockResolvedValue({ ok: true } as never);
});

describe("benefits tab", () => {
  it("with no benefits, is one muted line: no tiles of zeros, no prose", () => {
    const { container } = render(Benefits, { cards: [card()], showOwner: false, onchanged: vi.fn() });
    expect(screen.getByText("none yet")).toBeInTheDocument();
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
    await userEvent.type(screen.getByLabelText("Benefit"), "Priority Pass lounges");
    await userEvent.selectOptions(screen.getByLabelText("Kind"), "access");
    await userEvent.type(screen.getByLabelText(/Guests free/), "2");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(bodyOf(calls("/api/churning/cards/1/benefits")[0])).toMatchObject({ name: "Priority Pass lounges", kind: "access", guests: 2 }));
    expect(onclose).toHaveBeenCalledWith(true);
  });
});
