// @vitest-environment jsdom
import { render, screen, waitFor, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

import { api } from "$lib/api";
import { app } from "$lib/app.svelte";
import { toast } from "svelte-sonner";
import CardForm from "./CardForm.svelte";
import { benefit, bodyOf, calls, card, churning, found } from "./fixtures";

beforeEach(() => {
  vi.mocked(api).mockReset();
  vi.mocked(api).mockResolvedValue({ ok: true } as never);
  vi.mocked(toast).mockReset();
  vi.mocked(toast.error).mockReset();
});

describe("card form", () => {
  const setup = (c = null as ReturnType<typeof card> | null) => {
    const d = churning();
    render(CardForm, { c, d, person: "", onclose: vi.fn(), onchanged: vi.fn() });
    return d;
  };

  it("uses a dropdown of the people for whose card it is, and groups currencies by program", () => {
    setup();
    const owner = screen.getByLabelText("Whose card");
    expect(owner.tagName).toBe("SELECT");
    expect(within(owner).getAllByRole("option").map((o) => o.textContent)).toEqual(["Alex", "Sam"]);
    const earns = screen.getByLabelText("Earns");
    expect([...earns.querySelectorAll("optgroup")].map((g) => g.label)).toEqual(["Bank points", "Airline miles", "Hotel points", "Cash back"]);
  });

  describe("sections", () => {
    const section = (id: string) => screen.getByTestId(`section-${id}`) as HTMLDetailsElement;
    const summary = (id: string) => screen.getByTestId(`summary-${id}`);

    it("keeps the essentials up front and every other part closed, each saying what's in it", () => {
      setup();
      for (const id of ["rates", "bonus", "benefits", "plan", "more"]) expect(section(id).open).toBe(false);
      for (const label of ["Whose card", "Bank", "Card", "Opened", "Annual fee"]) expect(screen.getByLabelText(label).closest("details")).toBeNull();
      expect(summary("rates")).toHaveTextContent("1x on everything");
      expect(summary("bonus")).toHaveTextContent("None");
      expect(summary("benefits")).toHaveTextContent("None");
      expect(summary("plan")).toHaveTextContent("Undecided");
      expect(summary("more")).toHaveTextContent("Nothing added");
    });

    it("has no annual-fee month to pick", () => {
      setup();
      expect(screen.queryByLabelText("Fee posts in")).toBeNull();
      expect(screen.queryByText("Its anniversary month")).toBeNull();
    });

    it("says in the summaries what you filled in", async () => {
      setup();
      await userEvent.click(screen.getByRole("button", { name: "Add a rate" }));
      await userEvent.click(screen.getByRole("button", { name: "Add a rate" }));
      await userEvent.selectOptions(screen.getByLabelText("Earns"), "ur");
      await userEvent.type(screen.getByLabelText(/^Bonus \(/), "75000");
      await userEvent.type(screen.getByLabelText("Spend"), "4000");
      expect(summary("rates")).toHaveTextContent("2 rates · Chase Ultimate Rewards");
      expect(summary("bonus")).toHaveTextContent("75k Ultimate Rewards after $4,000 in 3 months");
      await userEvent.type(screen.getByLabelText(/Family/), "Sapphire");
      expect(summary("more")).toHaveTextContent("Family: Sapphire");
    });

    it("starts closed with summaries when editing a card too", () => {
      setup(card({ bonus: 60000, bonus_spend: 4000, currency: "ur", rates: [{ category: "Travel", multiplier: 5, portal_only: false }], benefits: [benefit()] }));
      expect(section("bonus").open).toBe(false);
      expect(summary("bonus")).toHaveTextContent("60k Ultimate Rewards after $4,000 in 3 months");
      expect(summary("rates")).toHaveTextContent("1 rate");
      expect(summary("benefits")).toHaveTextContent("1 benefit");
    });

    it("opens the section a refused save is about, and marks it", async () => {
      vi.mocked(api).mockRejectedValue(new Error("Pick a category for each earning rate"));
      setup();
      await userEvent.type(screen.getByLabelText("Card"), "Venture X");
      await userEvent.click(screen.getByRole("button", { name: "Add a rate" }));
      expect(section("rates").open).toBe(false);
      await userEvent.click(screen.getByRole("button", { name: "Add" }));
      await waitFor(() => expect(section("rates").open).toBe(true));
      expect(screen.getByTestId("flagged-rates")).toBeInTheDocument();
      expect(section("bonus").open).toBe(false);
    });

    it("opens the benefits section when a benefit is refused", async () => {
      vi.mocked(api).mockRejectedValue(new Error("Enter the benefit's name"));
      setup();
      await userEvent.type(screen.getByLabelText("Card"), "Venture X");
      await userEvent.click(screen.getByRole("button", { name: "Add" }));
      await waitFor(() => expect(section("benefits").open).toBe(true));
    });

    it("adds the benefits you enter in the request that adds the card", async () => {
      vi.mocked(api).mockResolvedValue({ id: 5 } as never);
      setup();
      await userEvent.type(screen.getByLabelText("Card"), "Sapphire Reserve");
      await userEvent.selectOptions(screen.getByLabelText("Add a benefit to this card"), "lounge");
      await userEvent.selectOptions(screen.getByLabelText("Add a benefit to this card"), "custom");
      await userEvent.type(screen.getByLabelText("Benefit 2"), "Travel credit");
      await userEvent.type(screen.getByLabelText("Amount of Travel credit"), "300");
      expect(summary("benefits")).toHaveTextContent("2 benefits");
      await userEvent.click(screen.getByRole("button", { name: "Add" }));
      await waitFor(() => expect(calls("/api/churning/cards")).toHaveLength(1));
      expect(bodyOf(calls("/api/churning/cards")[0])!.benefits).toEqual([
        { name: "Lounge access", kind: "access", amount: null, period: "annual", preset: "lounge" },
        { name: "Travel credit", kind: "credit", amount: 300, period: "annual" }]);
      expect(calls(/benefits/)).toHaveLength(0);
    });

    it("leaves out a benefit row with no name, and a removed one", async () => {
      setup();
      await userEvent.type(screen.getByLabelText("Card"), "Venture X");
      await userEvent.selectOptions(screen.getByLabelText("Add a benefit to this card"), "custom");
      await userEvent.selectOptions(screen.getByLabelText("Add a benefit to this card"), "lounge");
      await userEvent.click(screen.getByRole("button", { name: "Remove Lounge access" }));
      await userEvent.click(screen.getByRole("button", { name: "Add" }));
      await waitFor(() => expect(calls("/api/churning/cards")).toHaveLength(1));
      expect(bodyOf(calls("/api/churning/cards")[0])!.benefits).toEqual([]);
    });
  });

  it("offers portal-only just for travel rates", async () => {
    setup();
    await userEvent.click(screen.getByRole("button", { name: "Add a rate" }));
    expect(screen.queryByLabelText("Only through the issuer's travel portal")).not.toBeInTheDocument();   // no category yet
    await userEvent.selectOptions(screen.getByLabelText("Category of rate 1"), "Restaurants");
    expect(screen.queryByLabelText("Only through the issuer's travel portal")).not.toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Category of rate 1"), "Travel");
    expect(screen.getByLabelText("Only through the issuer's travel portal")).toBeInTheDocument();
  });

  it("sends the earning rates, portal-only ones included, in the request that adds the card", async () => {
    setup();
    await userEvent.type(screen.getByLabelText("Card"), "Venture X");
    await userEvent.click(screen.getByRole("button", { name: "Add a rate" }));
    await userEvent.selectOptions(screen.getByLabelText("Category of rate 1"), "Hotels");
    await userEvent.type(screen.getByLabelText("Points per dollar on Hotels"), "10");
    await userEvent.click(screen.getByLabelText("Only through the issuer's travel portal"));
    await userEvent.type(screen.getByLabelText("The portal's name"), "Capital One Travel");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(calls("/api/churning/cards")).toHaveLength(1));
    const body = bodyOf(calls("/api/churning/cards")[0])!;
    expect(body).toMatchObject({ owner: "Alex", product: "Venture X", portal_name: "Capital One Travel" });
    expect(body.rates).toEqual([{ category: "*", multiplier: 1, portal_only: false }, { category: "Hotels", multiplier: 10, portal_only: true }]);
    expect(body).not.toHaveProperty("base_rate");   // it travels inside rates
  });

  it("shows the server's message when the rates are refused", async () => {
    vi.mocked(api).mockRejectedValue(new Error("Pick a category for each earning rate"));
    setup();
    await userEvent.type(screen.getByLabelText("Card"), "Venture X");
    await userEvent.click(screen.getByRole("button", { name: "Add a rate" }));
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Pick a category for each earning rate");
    expect(toast.error).toHaveBeenCalledWith("Pick a category for each earning rate");
  });

  it("saves the whole list of rates when a row of an existing card changes", async () => {
    setup(card({ rates: [{ category: "Travel", multiplier: 5, portal_only: false }] }));
    const mult = screen.getByLabelText("Points per dollar on Travel");
    await userEvent.clear(mult);
    await userEvent.type(mult, "6");
    await userEvent.tab();
    await waitFor(() => expect(calls("/api/churning/cards/1")).toHaveLength(1));
    expect(bodyOf(calls("/api/churning/cards/1")[0])!.rates).toEqual([
      { category: "*", multiplier: 2, portal_only: false }, { category: "Travel", multiplier: 6, portal_only: false }]);
  });

  it("saves a plan and a hidden card as you change them", async () => {
    setup(card());
    await userEvent.selectOptions(screen.getByLabelText("What I'll do with it"), "product_change");
    await waitFor(() => expect(bodyOf(calls("/api/churning/cards/1").at(-1)!)).toEqual({ plan: "product_change" }));
    await userEvent.click(screen.getByLabelText("Don't show this card in Upcoming"));
    await waitFor(() => expect(bodyOf(calls("/api/churning/cards/1").at(-1)!)).toEqual({ hide_upcoming: true }));
    expect(screen.getByText(/the day before the next annual fee posts/)).toBeInTheDocument();
  });

  describe("from an account found on your accounts", () => {
    const draft = found();
    const open = () => render(CardForm, { c: null, d: churning(), person: "", draft, onclose: vi.fn(), onchanged: vi.fn() });

    it("starts pre-filled, says the opening day is a guess, and saves nothing until Add", async () => {
      open();
      expect(screen.getByLabelText("Card")).toHaveValue("Sapphire Reserve");
      expect(screen.getByLabelText("Whose card")).toHaveValue("Alex");
      expect(screen.getByLabelText("Bank")).toHaveValue("chase");
      expect(screen.getByLabelText("Annual fee")).toHaveValue(795);
      expect(screen.queryByLabelText("Fee posts in")).toBeNull();
      expect(screen.getByTestId("fee-month")).toHaveTextContent("The fee posts each March, the month the card was opened.");
      expect(screen.getByLabelText("Opened (on or before)")).toHaveValue("2024-03-02");
      expect(screen.getByTestId("opened-guess")).toHaveTextContent("opened on or before this day");
      expect(calls(/./)).toHaveLength(0);
      await userEvent.click(screen.getByRole("button", { name: "Add" }));
      await waitFor(() => expect(calls("/api/churning/cards")).toHaveLength(1));
      expect(bodyOf(calls("/api/churning/cards")[0])).toMatchObject({
        owner: "Alex", issuer: "chase", product: "Sapphire Reserve", account_id: "acct-1", opened_on: "2024-03-02", annual_fee: "795", business: false });
      expect(bodyOf(calls("/api/churning/cards")[0])).not.toHaveProperty("fee_month");
    });

    it("stops calling the date a guess once you change it", async () => {
      open();
      const opened = screen.getByLabelText("Opened (on or before)");
      await userEvent.clear(opened);
      await userEvent.type(opened, "2023-11-20");
      expect(screen.getByLabelText("Opened")).toHaveValue("2023-11-20");
      expect(screen.queryByTestId("opened-guess")).toBeNull();
    });

    it("asks for the day when the account has no transactions to guess from", () => {
      render(CardForm, { c: null, d: churning(), person: "", draft: found({ opened_on: null }), onclose: vi.fn(), onchanged: vi.fn() });
      expect(screen.getByTestId("opened-guess")).toHaveTextContent("can’t tell when this card was opened");
    });
  });

  describe("Fill in the rest with AI", () => {
    const suggestion = {
      family: "Sapphire", currency: "ur", base_rate: 1, annual_fee: 550, portal_name: "Chase Travel",
      rates: [{ category: "Travel", multiplier: 5, portal_only: 1 }, { category: "Restaurants", multiplier: 3, portal_only: 0 }],
      benefits: [{ name: "Travel credit", kind: "credit", amount: 300, period: "annual" }],
    };
    const setup = (c = null as ReturnType<typeof card> | null) => {
      vi.mocked(api).mockImplementation((async (path: string) => (path === "/api/churning/suggest" ? suggestion : path.endsWith("/cards") ? { id: 7 } : { ok: true })) as never);
      render(CardForm, { c, d: churning(), person: "", onclose: vi.fn(), onchanged: vi.fn() });
    };
    afterEach(() => { app.state = null; });

    it("is hidden without an OpenRouter key", () => {
      app.state = { connected: true, has_api_key: false } as never;
      setup();
      expect(screen.queryByRole("button", { name: "Fill in the rest with AI" })).toBeNull();
    });

    it("fills the empty fields, marks them, and saves them only with the card", async () => {
      app.state = { connected: true, has_api_key: true } as never;
      setup();
      const button = screen.getByRole("button", { name: "Fill in the rest with AI" });
      expect(button).toBeDisabled();   // it needs a name to ask about
      await userEvent.type(screen.getByLabelText("Card"), "Sapphire Reserve");
      await userEvent.click(button);
      expect(bodyOf(calls("/api/churning/suggest")[0])).toEqual({ issuer: "chase", product: "Sapphire Reserve" });   // the bank and the name only
      expect(await screen.findByTestId("ai-marked")).toHaveTextContent("Suggested by AI, check before saving");
      expect(screen.getByLabelText(/Family/)).toHaveValue("Sapphire");
      expect(screen.getByLabelText("Earns")).toHaveValue("ur");
      expect(screen.getByLabelText("Annual fee")).toHaveValue(550);
      expect(screen.getByLabelText("Category of rate 1")).toHaveValue("Travel");
      expect(screen.getByLabelText("The portal's name")).toHaveValue("Chase Travel");
      expect(screen.getByLabelText("Suggested benefit 1")).toHaveValue("Travel credit");
      expect(screen.getAllByText(/Suggested by AI, check before saving/).length).toBeGreaterThan(1);   // the banner, and the benefit
      for (const id of ["rates", "benefits", "more"]) expect((screen.getByTestId(`section-${id}`) as HTMLDetailsElement).open).toBe(true);   // the ones that got values
      expect((screen.getByTestId("section-bonus") as HTMLDetailsElement).open).toBe(false);
      expect(calls("/api/churning/cards")).toHaveLength(0);   // nothing saved yet
      await userEvent.clear(screen.getByLabelText("Amount of Travel credit"));
      await userEvent.type(screen.getByLabelText("Amount of Travel credit"), "250");   // editable
      await userEvent.click(screen.getByRole("button", { name: "Add" }));
      await waitFor(() => expect(calls("/api/churning/cards")).toHaveLength(1));
      const body = bodyOf(calls("/api/churning/cards")[0])!;
      expect(body).toMatchObject({ family: "Sapphire", currency: "ur", annual_fee: "550", portal_name: "Chase Travel" });
      expect(body.benefits).toEqual([{ name: "Travel credit", kind: "credit", amount: 250, period: "annual" }]);   // with the card, not after it
      expect(calls("/api/churning/cards/7/benefits")).toHaveLength(0);
    });

    it("keeps what you already entered, and Discard puts the form back", async () => {
      app.state = { connected: true, has_api_key: true } as never;
      setup();
      await userEvent.type(screen.getByLabelText("Card"), "Sapphire Reserve");
      await userEvent.type(screen.getByLabelText("Annual fee"), "95");
      await userEvent.click(screen.getByRole("button", { name: "Fill in the rest with AI" }));
      await screen.findByTestId("ai-marked");
      expect(screen.getByLabelText("Annual fee")).toHaveValue(95);
      await userEvent.click(screen.getByRole("button", { name: "Discard" }));
      expect(screen.queryByTestId("ai-marked")).toBeNull();
      expect(screen.getByLabelText(/Family/)).toHaveValue("");
      expect(screen.getByLabelText("Earns")).toHaveValue("cash");
      expect(screen.queryByLabelText("Suggested benefit 1")).toBeNull();
    });

    it("asking again doesn't suggest the same benefits twice", async () => {
      app.state = { connected: true, has_api_key: true } as never;
      setup();
      await userEvent.type(screen.getByLabelText("Card"), "Sapphire Reserve");
      await userEvent.click(screen.getByRole("button", { name: "Fill in the rest with AI" }));
      await screen.findByTestId("ai-marked");
      await userEvent.click(screen.getByRole("button", { name: "Fill in the rest with AI" }));
      await waitFor(() => expect(calls("/api/churning/suggest")).toHaveLength(2));
      expect(within(screen.getByTestId("ai-benefits")).getAllByRole("listitem")).toHaveLength(1);
    });

    it("Discard takes back only what the AI filled, not what you changed afterwards", async () => {
      app.state = { connected: true, has_api_key: true } as never;
      setup();
      await userEvent.type(screen.getByLabelText("Card"), "Sapphire Reserve");
      await userEvent.click(screen.getByRole("button", { name: "Fill in the rest with AI" }));
      await screen.findByTestId("ai-marked");
      await userEvent.type(screen.getByLabelText(/^Bonus \(/), "60000");   // typed after the fill
      await userEvent.clear(screen.getByLabelText(/Family/));
      await userEvent.type(screen.getByLabelText(/Family/), "Sapphire cards");   // an AI field you corrected
      await userEvent.click(screen.getByRole("button", { name: "Discard" }));
      expect(screen.getByLabelText(/^Bonus \(/)).toHaveValue(60000);
      expect(screen.getByLabelText(/Family/)).toHaveValue("Sapphire cards");
      expect(screen.getByLabelText("Earns")).toHaveValue("cash");   // untouched AI fields go back
      expect(screen.getByLabelText("Annual fee")).toHaveValue(null);
      expect(screen.queryByLabelText("Category of rate 1")).toBeNull();
    });

    it("on an existing card, saves only when you say so", async () => {
      app.state = { connected: true, has_api_key: true } as never;
      setup(card({ currency: "cash", annual_fee: 0, rates: [] }));
      await userEvent.click(screen.getByRole("button", { name: "Fill in the rest with AI" }));
      await screen.findByTestId("ai-marked");
      expect(calls("/api/churning/cards/1")).toHaveLength(0);
      await userEvent.click(screen.getByRole("button", { name: "Save these" }));
      await waitFor(() => expect(calls("/api/churning/cards/1/benefits")).toHaveLength(1));
      expect(bodyOf(calls("/api/churning/cards/1")[0])).toMatchObject({ family: "Sapphire", currency: "ur", annual_fee: "550" });
      expect(screen.queryByTestId("ai-marked")).toBeNull();
    });

    it("on an existing card, Save these sends what the form holds, not what the AI first said", async () => {
      app.state = { connected: true, has_api_key: true } as never;
      setup(card({ currency: "cash", annual_fee: 0, rates: [] }));
      await userEvent.click(screen.getByRole("button", { name: "Fill in the rest with AI" }));
      await screen.findByTestId("ai-marked");
      await userEvent.clear(screen.getByLabelText("Annual fee"));
      await userEvent.type(screen.getByLabelText("Annual fee"), "95");   // the AI said 550
      await userEvent.click(screen.getByRole("button", { name: "Save these" }));
      await waitFor(() => expect(calls("/api/churning/cards/1/benefits")).toHaveLength(1));
      const saved = calls("/api/churning/cards/1").map(bodyOf).find((b) => "family" in (b as object));
      expect(saved).toMatchObject({ family: "Sapphire" });
      expect(Number((saved as { annual_fee: unknown }).annual_fee)).toBe(95);
    });

    it("shows an error as a toast and leaves the form alone", async () => {
      app.state = { connected: true, has_api_key: true } as never;
      vi.mocked(api).mockRejectedValue(new Error("The AI request failed after 45s: timed out"));
      render(CardForm, { c: null, d: churning(), person: "", onclose: vi.fn(), onchanged: vi.fn() });
      await userEvent.type(screen.getByLabelText("Card"), "Sapphire Reserve");
      await userEvent.click(screen.getByRole("button", { name: "Fill in the rest with AI" }));
      await waitFor(() => expect(toast.error).toHaveBeenCalledWith("The AI request failed after 45s: timed out"));
      expect(screen.queryByTestId("ai-marked")).toBeNull();
      expect(screen.getByRole("button", { name: "Fill in the rest with AI" })).toBeEnabled();
    });
  });
});
