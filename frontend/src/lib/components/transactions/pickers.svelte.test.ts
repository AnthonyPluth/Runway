// @vitest-environment jsdom
// The small transaction popups: RecurringPicker, RememberBar, LogoPicker and Upcoming.
import { render, screen } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn(), dismiss: vi.fn() }) }));

import { api } from "$lib/api";
import { toast } from "svelte-sonner";
import { tx } from "../../../test/fixtures";
import LogoPicker from "./LogoPicker.svelte";
import RecurringPicker from "./RecurringPicker.svelte";
import RememberBar from "./RememberBar.svelte";
import { askAlsoMatch, askRemember, closeRemember, remember } from "./remember.svelte";
import Upcoming from "./Upcoming.svelte";
import { createRawSnippet } from "svelte";

// (braces: a returned function would be run by Vitest as a cleanup hook)
beforeEach(() => { vi.mocked(api).mockReset(); });
afterEach(() => vi.clearAllMocks());

describe("RecurringPicker", () => {
  const items = [
    { id: 1, name: "Coffee club", frequency: "monthly", account_id: "a1", amount: -20, active: 1, matched_count: 0 },
    { id: 2, name: "Gym", frequency: "weekly", account_id: "a2", amount: -20, active: 1, matched_count: 0 },
  ];
  const setup = (t = tx(), extra = {}) => {
    const cbs = { onclose: vi.fn(), onchanged: vi.fn() };
    render(RecurringPicker, { t, items, ...cbs, ...extra });
    return cbs;
  };
  const select = () => screen.getByRole("combobox", { name: "Recurring item for this transaction" });

  it("lists this account's items first and other accounts' separately", () => {
    setup();
    expect(screen.getByRole("group", { name: "Link to" })).toHaveTextContent("Coffee club · monthly");
    expect(screen.getByRole("group", { name: "Other accounts" })).toHaveTextContent("Gym · weekly");
  });

  it("offers 'Not recurring' only when it's currently linked", () => {
    setup(tx({ recurring_id: 1 }));
    expect(screen.getByRole("option", { name: "Not recurring" })).toBeInTheDocument();
  });

  it("doesn't offer 'Not recurring' when it isn't linked", () => {
    setup();
    expect(screen.queryByRole("option", { name: "Not recurring" })).not.toBeInTheDocument();
  });

  it("links to an existing item", async () => {
    vi.mocked(api).mockResolvedValue({});
    const { onclose, onchanged } = setup();
    await userEvent.selectOptions(select(), "1");
    expect(api).toHaveBeenCalledWith("/api/transactions/t1/recurring", { method: "POST", body: { recurring_id: 1 } });
    expect(toast.success).toHaveBeenCalledWith("Linked");
    expect(onclose).toHaveBeenCalled();
    expect(onchanged).toHaveBeenCalled();
  });

  it("starts a new recurring item from the transaction", async () => {
    vi.mocked(api).mockResolvedValue({});
    setup();
    await userEvent.selectOptions(select(), "new:weekly");
    expect(api).toHaveBeenCalledWith("/api/transactions/t1/recurring", { method: "POST", body: { new: "weekly" } });
    expect(toast.success).toHaveBeenCalledWith("Recurring item created; edit it in Recurring");
  });

  it("offers to match the transaction's text from now on when none of the item's is on it", async () => {
    vi.mocked(api).mockResolvedValue({ ok: true, suggest_text: "online transfer from savings" });
    const { onchanged } = setup();
    await userEvent.selectOptions(select(), "1");
    expect(remember.match).toEqual({ recurringId: 1, name: "Coffee club", text: "online transfer from savings", reload: onchanged });
    expect(toast.success).not.toHaveBeenCalled();   // the question says it's linked
    closeRemember();
  });

  it("marks a transaction as not recurring", async () => {
    vi.mocked(api).mockResolvedValue({});
    setup(tx({ recurring_id: 1 }));
    await userEvent.selectOptions(select(), "none");
    expect(api).toHaveBeenCalledWith("/api/transactions/t1/recurring", { method: "POST", body: { recurring_id: null } });
    expect(toast.success).toHaveBeenCalledWith("Marked as not recurring");
  });

  it("closes without doing anything when you leave it alone", async () => {
    const { onclose } = setup();
    select().focus();
    await userEvent.tab();
    expect(onclose).toHaveBeenCalled();
    expect(api).not.toHaveBeenCalled();
  });

  it("shows the error but still closes when linking fails", async () => {
    vi.mocked(api).mockRejectedValue(new Error("Nope"));
    const { onclose } = setup();
    await userEvent.selectOptions(select(), "1");
    expect(toast.error).toHaveBeenCalledWith("Nope");
    expect(onclose).toHaveBeenCalled();
  });
});

describe("RememberBar", () => {
  afterEach(() => closeRemember());

  it("is hidden until a category was just picked", () => {
    render(RememberBar);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("asks whether to use the category for the merchant from now on", async () => {
    render(RememberBar);
    askRemember("t1", "Coffee", { merchant: "Blue Bottle", replaces: "Dining" }, vi.fn());
    expect(await screen.findByRole("dialog")).toHaveTextContent("Saved. Always use Coffee for Blue Bottle?(instead of Dining)");
  });

  it("'Just this once' closes it without a rule", async () => {
    render(RememberBar);
    askRemember("t1", "Coffee", { merchant: "Blue Bottle" }, vi.fn());
    await userEvent.click(await screen.findByRole("button", { name: "Just this once" }));
    expect(remember.ask).toBeNull();
    expect(api).not.toHaveBeenCalled();
  });

  it("'Always' saves it as a rule", async () => {
    vi.mocked(api).mockResolvedValue({ also_updated: 0 });
    render(RememberBar);
    askRemember("t1", "Coffee", { merchant: "Blue Bottle" }, vi.fn());
    await userEvent.click(await screen.findByRole("button", { name: "Always" }));
    expect(api).toHaveBeenCalledWith("/api/transactions/t1/category", { method: "POST", body: { category: "Coffee", remember: true } });
  });

  it("asks whether to match a linked transaction's text from now on, and adds it when you say so", async () => {
    vi.mocked(api).mockResolvedValue({ ok: true, linked: 2 });
    const reload = vi.fn();
    render(RememberBar);
    askAlsoMatch(1, "Paycheck", "online transfer from savings", reload);
    expect(await screen.findByRole("dialog")).toHaveTextContent("Linked to Paycheck. Also match “online transfer from savings” from now on?");
    await userEvent.click(screen.getByRole("button", { name: "Also match" }));
    expect(api).toHaveBeenCalledWith("/api/recurring/1/match", { method: "POST", body: { text: "online transfer from savings" } });
    expect(toast.success).toHaveBeenCalledWith("Paycheck also matches “online transfer from savings” now · 2 more linked");
    expect(reload).toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("'Just this one' leaves the item's texts alone; a failure to add says so", async () => {
    render(RememberBar);
    askAlsoMatch(1, "Paycheck", "churn", vi.fn());
    await userEvent.click(await screen.findByRole("button", { name: "Just this one" }));
    expect(remember.match).toBeNull();
    expect(api).not.toHaveBeenCalled();
    vi.mocked(api).mockRejectedValue(new Error("Use at least three letters of text"));
    askAlsoMatch(1, "Paycheck", "churn", vi.fn());
    await userEvent.click(await screen.findByRole("button", { name: "Also match" }));
    expect(toast.error).toHaveBeenCalledWith("Use at least three letters of text");
  });

  it("closes on Escape", async () => {
    render(RememberBar);
    askRemember("t1", "Coffee", { merchant: "Blue Bottle" }, vi.fn());
    await screen.findByRole("dialog");
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("LogoPicker", () => {
  const logo = createRawSnippet(() => ({ render: () => "<span>LOGO</span>" }));
  const options = (extra = {}) => ({ choice: null, searchable: true, configured: true, candidates: [{ name: "Target", domain: "target.com" }], error: null, ...extra });
  const setup = () => { const onchanged = vi.fn(); render(LogoPicker, { name: "Target", children: logo, onchanged }); return onchanged; };

  it("shows its content and stays closed until clicked", () => {
    setup();
    expect(screen.getByText("LOGO")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("looks up Logo.dev's matches for the merchant name when opened", async () => {
    vi.mocked(api).mockResolvedValue(options());
    setup();
    await userEvent.click(screen.getByRole("button", { name: "Logo for Target" }));
    expect(api).toHaveBeenCalledWith("/api/merchants/logo-options?name=Target");
    expect(await screen.findByRole("button", { name: /Target\s*target\.com/ })).toBeInTheDocument();
  });

  it("uses a match for every transaction from that merchant", async () => {
    vi.mocked(api).mockResolvedValueOnce(options()).mockResolvedValueOnce({});
    const onchanged = setup();
    await userEvent.click(screen.getByRole("button", { name: "Logo for Target" }));
    await userEvent.click(await screen.findByRole("button", { name: /target\.com/ }));
    expect(api).toHaveBeenLastCalledWith("/api/merchants/logo", { method: "POST", body: { name: "Target", website: "target.com" } });
    expect(toast.success).toHaveBeenCalledWith("Using target.com's logo for every Target transaction");
    expect(onchanged).toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("accepts a website typed in", async () => {
    vi.mocked(api).mockResolvedValueOnce(options({ candidates: [] })).mockResolvedValueOnce({});
    setup();
    await userEvent.click(screen.getByRole("button", { name: "Logo for Target" }));
    const use = await screen.findByRole("button", { name: "Use" });
    expect(use).toBeDisabled();
    await userEvent.type(screen.getByRole("textbox", { name: "The merchant's website" }), "corp.target.com{Enter}");
    expect(api).toHaveBeenLastCalledWith("/api/merchants/logo", { method: "POST", body: { name: "Target", website: "corp.target.com" } });
  });

  it("can hide the logo, or go back to Runway's own pick", async () => {
    vi.mocked(api).mockResolvedValueOnce(options({ choice: { website: "target.com", hidden: false } })).mockResolvedValue({});
    setup();
    await userEvent.click(screen.getByRole("button", { name: "Logo for Target" }));
    await userEvent.click(await screen.findByRole("button", { name: "Automatic" }));
    expect(api).toHaveBeenLastCalledWith("/api/merchants/logo", { method: "POST", body: { name: "Target" } });
  });

  it("chooses an account's logo, with Logo.dev's matches for its institution", async () => {
    const chase = options({ candidates: [{ name: "Chase", domain: "chase.com" }] });
    vi.mocked(api).mockResolvedValueOnce(chase).mockResolvedValueOnce({}).mockResolvedValueOnce(chase).mockResolvedValue({});
    const onchanged = vi.fn();
    render(LogoPicker, { name: "CSR", account: "a 1", children: logo, onchanged });
    await userEvent.click(screen.getByRole("button", { name: "Logo for CSR" }));
    expect(api).toHaveBeenCalledWith("/api/accounts/a%201/logo-options");
    await userEvent.click(await screen.findByRole("button", { name: /chase\.com/ }));
    expect(api).toHaveBeenLastCalledWith("/api/accounts/a%201/logo", { method: "POST", body: { website: "chase.com" } });
    expect(toast.success).toHaveBeenCalledWith("Using chase.com's logo for CSR");
    expect(onchanged).toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Logo for CSR" }));
    await userEvent.click(await screen.findByRole("button", { name: "No logo" }));
    expect(api).toHaveBeenLastCalledWith("/api/accounts/a%201/logo", { method: "POST", body: { hidden: true } });
  });

  it("chooses a holding's logo by its group, with Logo.dev's matches for its name", async () => {
    const fund = options({ candidates: [{ name: "Vanguard", domain: "vanguard.com" }] });
    vi.mocked(api).mockResolvedValueOnce(fund).mockResolvedValueOnce({}).mockResolvedValueOnce(fund).mockResolvedValue({});
    const onchanged = vi.fn();
    render(LogoPicker, { name: "Made-Up Fund", holding: "t:MUF 1", children: logo, onchanged });
    await userEvent.click(screen.getByRole("button", { name: "Logo for Made-Up Fund" }));
    expect(api).toHaveBeenCalledWith("/api/investments/logo-options?group=t%3AMUF%201&name=Made-Up%20Fund");
    await userEvent.click(await screen.findByRole("button", { name: /vanguard\.com/ }));
    expect(api).toHaveBeenLastCalledWith("/api/investments/logo", { method: "POST", body: { group: "t:MUF 1", website: "vanguard.com" } });
    expect(toast.success).toHaveBeenCalledWith("Using vanguard.com's logo for Made-Up Fund");
    expect(onchanged).toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Logo for Made-Up Fund" }));
    await userEvent.click(await screen.findByRole("button", { name: "No logo" }));
    expect(api).toHaveBeenLastCalledWith("/api/investments/logo", { method: "POST", body: { group: "t:MUF 1", hidden: true } });
  });

  it("says how to pick by website when there's no Logo.dev key", async () => {
    vi.mocked(api).mockResolvedValue(options({ configured: false, candidates: [] }));
    setup();
    await userEvent.click(screen.getByRole("button", { name: "Logo for Target" }));
    expect(await screen.findByText(/Add a Logo\.dev key/)).toBeInTheDocument();
  });

  it("closes on Escape or a click elsewhere", async () => {
    vi.mocked(api).mockResolvedValue(options());
    setup();
    await userEvent.click(screen.getByRole("button", { name: "Logo for Target" }));
    await screen.findByRole("dialog");
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Logo for Target" }));
    await screen.findByRole("dialog");
    await userEvent.click(document.body);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("reports a failed lookup and closes", async () => {
    vi.mocked(api).mockRejectedValue(new Error("No key"));
    setup();
    await userEvent.click(screen.getByRole("button", { name: "Logo for Target" }));
    await vi.waitFor(() => expect(toast.error).toHaveBeenCalledWith("No key"));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("Upcoming", () => {
  const ev = { date: "2026-03-15", name: "Rent", amount: -1500, kind: "recurring", key: "rent-1", balance_after: 900 };

  it("shows nothing when nothing is projected", () => {
    const { container } = render(Upcoming, { props: { onchanged: vi.fn(), events: [] } });
    expect(container.textContent?.trim()).toBe("");
  });

  it("lists projected items under a heading, with the account since several are shown together", () => {
    render(Upcoming, { props: { onchanged: vi.fn(), events: [{ ...ev, account: "Checking" }] } });
    expect(screen.getByRole("heading", { name: "Upcoming · projected" })).toBeInTheDocument();
    expect(screen.getByText("Rent")).toBeInTheDocument();
    expect(screen.getAllByText(/Checking/).length).toBeGreaterThan(0);
  });
});
