// @vitest-environment jsdom
import { render, screen, waitFor, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

import { api } from "$lib/api";
import { toast } from "svelte-sonner";
import BankForm from "./BankForm.svelte";
import BenefitForm from "./BenefitForm.svelte";
import { bankBonus, benefit, bodyOf, calls, churning, wish } from "./fixtures";
import type { BankBonus } from "./types";
import Harness from "./ListsHarness.test.svelte";
import WishForm from "./WishForm.svelte";

beforeEach(() => {
  vi.mocked(api).mockReset();
  vi.mocked(api).mockResolvedValue({ ok: true } as never);
  vi.mocked(toast).mockReset();
  vi.mocked(toast.error).mockReset();
});

const section = (id: string) => screen.getByTestId(`section-${id}`) as HTMLDetailsElement;

describe("the bank bonus form", () => {
  const setup = (b: BankBonus | null = null, onclose = vi.fn()) => { render(BankForm, { b, d: churning(), person: "", onclose }); return onclose; };

  it("has no blank leftover lines between its title and its first row", () => {
    setup();
    const title = screen.getByRole("heading", { name: "Add a bank bonus" });
    expect(title.nextElementSibling!.className).toContain("mt-3");
  });

  it("asks for the bank and the bonus, marks them, focuses the first and sends nothing", async () => {
    setup();
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    const bank = screen.getByLabelText(/^Bank/);
    expect(bank).toHaveAttribute("aria-invalid", "true");
    expect(bank).toHaveAccessibleDescription("Enter the bank, like Chase.");
    expect(screen.getByLabelText(/^Bonus/)).toHaveAccessibleDescription("Enter the bonus.");
    expect(bank).toHaveFocus();
    expect(calls("/api/churning/bank")).toHaveLength(0);
    await userEvent.type(bank, "Chase");
    await userEvent.type(screen.getByLabelText(/^Bonus/), "300");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(bodyOf(calls("/api/churning/bank")[0])).toMatchObject({ bank: "Chase", bonus: 300, owner: "Alex" }));
  });

  it("opens the section a refusal is about, flags it, and says it there and by the Add button", async () => {
    vi.mocked(api).mockRejectedValue(new Error("Pick one of your checking or savings accounts"));
    setup();
    await userEvent.type(screen.getByLabelText(/^Bank/), "Chase");
    await userEvent.type(screen.getByLabelText(/^Bonus/), "300");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(section("requirements").open).toBe(true));
    expect(screen.getByTestId("flagged-requirements")).toBeInTheDocument();
    expect(screen.getByTestId("error-requirements")).toHaveTextContent("Pick one of your checking or savings accounts");
    expect(screen.getByRole("alert")).toHaveTextContent("Pick one of your checking or savings accounts");
    expect(section("fees").open).toBe(false);
    expect(toast.error).not.toHaveBeenCalled();
  });

  it("sends a refusal about fees or the day it posted to those sections", async () => {
    setup();
    await userEvent.type(screen.getByLabelText(/^Bank/), "Chase");
    await userEvent.type(screen.getByLabelText(/^Bonus/), "300");
    vi.mocked(api).mockRejectedValueOnce(new Error("The monthly fee has to be a number"));
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(section("fees").open).toBe(true));
    vi.mocked(api).mockRejectedValueOnce(new Error("The bonus can't post before the account was opened"));
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(section("received").open).toBe(true));
    expect(screen.getByTestId("flagged-received")).toBeInTheDocument();
    expect(screen.queryByTestId("flagged-fees")).toBeNull();
  });

  it("closes an edited bonus with Close, and deleting asks first", async () => {
    const onclose = setup(bankBonus({ id: 4 }));
    expect(screen.queryByRole("button", { name: "Done" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    const dialog = await screen.findByRole("dialog", { name: "Delete Chase?" });
    expect(calls(/remove/)).toHaveLength(0);
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(calls("/api/churning/bank/4/remove")).toHaveLength(1));
    await waitFor(() => expect(onclose).toHaveBeenCalledWith(true));
  });

  it("leaves the dialog open and says why when a delete is refused", async () => {
    setup(bankBonus({ id: 4 }));
    vi.mocked(api).mockRejectedValue(new Error("Bank bonus not found"));
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    await userEvent.click(within(await screen.findByRole("dialog", { name: "Delete Chase?" })).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Bank bonus not found"));
    expect(screen.getByRole("dialog", { name: "Delete Chase?" })).toBeInTheDocument();
  });

  it("keeps the Add button in a footer that is sticky on phones", () => {
    setup();
    expect(screen.getByTestId("form-footer").className).toContain("phone:sticky");
  });
});

describe("the plan form", () => {
  const setup = (w = null as ReturnType<typeof wish> | null, onclose = vi.fn()) => { render(WishForm, { w, d: churning(), person: "", onclose }); return onclose; };

  it("asks for the card's name, then for the bank when planning a bank bonus", async () => {
    setup();
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(screen.getByLabelText(/^Card/)).toHaveAccessibleDescription("Enter the card’s name, like Sapphire Preferred.");
    expect(screen.getByLabelText(/^Card/)).toHaveFocus();
    await userEvent.selectOptions(screen.getByLabelText("What"), "bank_bonus");
    expect(screen.getByLabelText(/^Bank/)).toHaveAccessibleDescription("Enter the bank, like Chase.");
    expect(calls("/api/churning/wishlist")).toHaveLength(0);
  });

  it("opens the timing section when the server refuses a date, and says it by Add and in the section", async () => {
    vi.mocked(api).mockRejectedValue(new Error("Enter a valid day the offer ends"));
    setup();
    await userEvent.type(screen.getByLabelText(/^Card/), "Gold");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(section("timing").open).toBe(true));
    expect(screen.getByTestId("flagged-timing")).toBeInTheDocument();
    expect(screen.getByTestId("error-timing")).toHaveTextContent("Enter a valid day the offer ends");
    expect(screen.getByRole("alert")).toHaveTextContent("Enter a valid day the offer ends");
    expect(section("expect").open).toBe(false);
  });

  it("opens What you expect when the server refuses an amount", async () => {
    vi.mocked(api).mockRejectedValue(new Error("The annual fee has to be a number"));
    setup();
    await userEvent.type(screen.getByLabelText(/^Card/), "Gold");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(section("expect").open).toBe(true));
    expect(screen.getByTestId("flagged-expect")).toBeInTheDocument();
  });

  it("closes an edited item with Close, and Delete asks first", async () => {
    const onclose = setup(wish({ id: 6 }));
    expect(screen.queryByRole("button", { name: "Done" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    await userEvent.click(within(await screen.findByRole("dialog", { name: "Delete Sapphire Preferred?" })).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(calls("/api/churning/wishlist/6/remove")).toHaveLength(1));
    await waitFor(() => expect(onclose).toHaveBeenCalledWith(true));
  });
});

describe("the benefit form", () => {
  const setup = (b = null as ReturnType<typeof benefit> | null, onclose = vi.fn()) => { render(BenefitForm, { card: { id: 1, product: "Venture X" }, d: churning(), b, onclose }); return onclose; };

  it("asks for the name and a credit's amount not below 0", async () => {
    setup();
    await userEvent.type(screen.getByLabelText(/^Amount/), "-5");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(screen.getByLabelText(/^Benefit/)).toHaveAccessibleDescription("Enter the benefit’s name, like Lyft credit.");
    expect(screen.getByLabelText(/^Amount/)).toHaveAccessibleDescription("The amount can’t be below 0.");
    expect(screen.getByLabelText(/^Benefit/)).toHaveFocus();
    expect(calls(/benefits/)).toHaveLength(0);
  });

  it("shows a refusal by the Add button", async () => {
    vi.mocked(api).mockRejectedValue(new Error("Pick how often it resets"));
    setup();
    await userEvent.type(screen.getByLabelText(/^Benefit/), "Lyft credit");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Pick how often it resets");
  });

  it("closes with Close, and Delete says the history goes too and asks first", async () => {
    const onclose = setup(benefit({ id: 9 }));
    expect(screen.queryByRole("button", { name: "Done" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    const dialog = await screen.findByRole("dialog", { name: "Delete Travel credit?" });
    expect(dialog).toHaveTextContent("history of uses");
    expect(calls(/remove/)).toHaveLength(0);
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(calls("/api/churning/benefits/9/remove")).toHaveLength(1));
    await waitFor(() => expect(onclose).toHaveBeenCalledWith(true));
  });
});

describe("lists a form edits", () => {
  it("keeps each benefit row's own input when one above it is removed", async () => {
    render(Harness, { d: churning(), which: "benefits" });
    const second = screen.getByLabelText("Benefit 2");
    await userEvent.click(screen.getByRole("button", { name: "Remove Lyft credit" }));
    expect(screen.getByLabelText("Benefit 1")).toBe(second);
    expect(second).toHaveValue("Lounge access");
  });

  it("keeps each earning rate's own fields when one above it is removed", async () => {
    render(Harness, { d: churning(), which: "rates" });
    const second = screen.getByLabelText("Points per dollar on Restaurants");
    await userEvent.click(screen.getByRole("button", { name: "Remove the Travel rate" }));
    expect(screen.getByLabelText("Points per dollar on Restaurants")).toBe(second);
    expect(second).toHaveValue(3);
  });
});
