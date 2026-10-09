// @vitest-environment jsdom
import { render, screen, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn().mockResolvedValue({}), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

import { api } from "$lib/api";
import { holding } from "../../../test/fixtures";
import HoldingsTable from "./HoldingsTable.svelte";
import { inv } from "./state.svelte";

const lot = (extra = {}) => ({ account_id: "acc1", security_id: "s1", account_name: "Brokerage", quantity: 10, value: 2500, cost_basis: 2000,
  reported_cost_basis: 2000, per_share: 200, manual: false, ...extra });
const rowsText = () => screen.getAllByRole("row").filter((r) => !r.hasAttribute("data-editor")).slice(1).map((r) => { const td = r.querySelector("td")!.cloneNode(true) as HTMLElement; td.querySelectorAll("[aria-hidden]").forEach((n) => n.remove()); return td.textContent!.replace(/\s+/g, " ").trim(); });
const setup = (holdings = [holding()]) => {
  const onchanged = vi.fn();
  render(HoldingsTable, { holdings, onchanged });
  return onchanged;
};

beforeEach(() => { inv.sort = { key: "value", dir: -1 }; vi.mocked(api).mockClear(); });

describe("HoldingsTable", () => {
  it("shows each holding's logo when the API gave one, and its letter otherwise", () => {
    setup([holding({ group: "t:VTI", logo: "/api/merchants/ticker%3AVTI/logo" }), holding({ security_id: "s2", group: "t:XYZ", ticker: "XYZ", name: "XYZ Corp", logo: null })]);
    const [vti, xyz] = screen.getAllByRole("row").slice(1);
    expect(vti.querySelector("img")).toHaveAttribute("src", "/api/merchants/ticker%3AVTI/logo");
    expect(xyz.querySelector("img")).toBeNull();
    expect(xyz.querySelector("td span[aria-hidden]")).toHaveTextContent("X");
  });

  it("opens the logo chooser from a holding's logo, and reloads after a choice", async () => {
    vi.mocked(api).mockResolvedValueOnce({ choice: null, searchable: false, configured: true, candidates: [], error: null }).mockResolvedValue({});
    const onchanged = setup([holding({ group: "t:VTI" })]);
    await userEvent.click(screen.getByRole("button", { name: "Logo for Vanguard Total Market" }));
    expect(api).toHaveBeenCalledWith("/api/investments/logo-options?group=t%3AVTI&name=Vanguard%20Total%20Market");
    await userEvent.click(await screen.findByRole("button", { name: "No logo" }));
    expect(api).toHaveBeenLastCalledWith("/api/investments/logo", { method: "POST", body: { group: "t:VTI", hidden: true } });
    expect(onchanged).toHaveBeenCalledTimes(1);
  });

  it("keeps a holding's logo chooser to the holdings that aren't cash", () => {
    setup([holding({ group: "t:VTI" }), holding({ security_id: "s9", group: "s9", ticker: "CUR:USD", name: "US Dollar", is_cash: true })]);
    expect(screen.getAllByRole("button", { name: /^Logo for / })).toHaveLength(1);
  });

  it("shows a holding's ticker, name, accounts, shares, price and value", () => {
    setup();
    const row = screen.getAllByRole("row")[1];
    expect(row).toHaveTextContent("VTI Vanguard Total Market");
    expect(row).toHaveTextContent("Brokerage");
    expect(row).toHaveTextContent("10");
    expect(row).toHaveTextContent("$250.00");
    expect(row).toHaveTextContent("$2,500.00");
  });

  it("colours gains green and losses red, each signed with a real minus", () => {
    setup([holding({ gain: 500, gain_pct: 0.25, day_change: -30, day_change_pct: -0.012 })]);
    const row = screen.getAllByRole("row")[1];
    expect(within(row).getByText("+$500.00")).toHaveClass("text-good");
    expect(within(row).getByText("+25.0%")).toHaveClass("text-good");
    expect(within(row).getByText("−$30.00")).toHaveClass("text-loss");
    expect(within(row).getByText("−1.20%")).toHaveClass("text-loss");
  });

  it("centres the total gain header over its values", () => {
    setup([holding({ gain: 500, gain_pct: 0.25 })]);
    expect(screen.getByRole("columnheader", { name: /Total gain/ })).toHaveClass("text-center");
    expect(within(screen.getAllByRole("row")[1]).getByText("+$500.00").closest("td")).toHaveClass("text-center");
  });

  it("doesn't repeat the day change under the holding at desktop width, where it has its own column", () => {
    setup([holding({ day_change: -30, day_change_pct: -0.012 })]);
    expect(screen.getAllByRole("row")[1].querySelector("[data-day-change]")).toBeNull();
  });

  it("shows a dash instead of a gain when the cost basis is unknown, and offers to add it", () => {
    setup([holding({ gain: null, gain_pct: null, day_change: null, day_change_pct: null, cost_known: false })]);
    const row = screen.getAllByRole("row")[1];
    expect(within(row).getAllByText("—")).toHaveLength(2);
    expect(within(row).getByRole("button", { name: /Add/ })).toBeInTheDocument();
  });

  it("shows cash without a share count, price or cost basis", () => {
    setup([holding({ is_cash: true, ticker: null, name: "Cash", price: null, gain: null, gain_pct: null })]);
    const row = screen.getAllByRole("row")[1];
    expect(within(row).getAllByText("—").length).toBeGreaterThanOrEqual(3);
    expect(within(row).queryByTitle("Edit cost basis")).not.toBeInTheDocument();
  });

  it("marks a live price, and an edited cost basis", () => {
    setup([holding({ live: true, live_time: 1_700_000_000, cost_manual: true })]);
    expect(screen.getByRole("img", { name: "Live price" })).toHaveAttribute("title", expect.stringContaining("Live price"));
    expect(screen.getByText("edited")).toBeInTheDocument();
  });

  it("hides a ticker that is really an exchange-qualified code", () => {
    setup([holding({ ticker: "NASDAQ:VTI" })]);
    expect(screen.getAllByRole("row")[1].querySelector("b")).toHaveTextContent("");
  });

  describe("on a wide screen (1280px and up)", () => {
    const queries: string[] = [];
    beforeEach(() => {
      queries.length = 0;
      vi.stubGlobal("matchMedia", (q: string) => { queries.push(q); return { matches: false, media: q, addEventListener: vi.fn(), removeEventListener: vi.fn() }; });
    });
    afterEach(() => vi.unstubAllGlobals());

    it("shows every column, with cost basis in its own cell and total gain centred", () => {
      setup([holding({ value: 2500, gain: 500, gain_pct: 0.25 })]);
      expect(queries).toContain("(max-width: 1279px)");
      expect(screen.getAllByRole("columnheader")).toHaveLength(8);
      const row = screen.getAllByRole("row")[1];
      const costCell = within(row).getByTitle("Edit cost basis").closest("td") as HTMLTableCellElement;
      expect(costCell.cellIndex).toBe(7);   // its own last column, not folded into the holding cell
      const gainCell = within(row).getByText("+$500.00").closest("td")!;
      expect(gainCell).toHaveClass("text-center");
      expect(screen.getByRole("columnheader", { name: /Total gain/ })).toHaveClass("text-center");
    });
  });

  describe("on a tablet (768px to 1279px)", () => {
    beforeEach(() => vi.stubGlobal("matchMedia", (q: string) => ({ matches: q === "(max-width: 1279px)", media: q, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
    afterEach(() => vi.unstubAllGlobals());

    it("keeps Value beside the holding, before Total gain", () => {
      setup([holding({ value: 2500, gain: 500, gain_pct: 0.25 })]);
      const heads = screen.getAllByRole("columnheader").map((h) => h.getAttribute("data-col"));
      expect(heads.indexOf("value")).toBeLessThan(heads.indexOf("gain"));
      expect(within(screen.getAllByRole("row")[1]).getByText("$2,500.00").closest("td")!.cellIndex).toBe(heads.indexOf("value"));
    });

    it("shows each holding's day change in dollars and percent on a line under its quantity, coloured", () => {
      setup([holding({ day_change: -30, day_change_pct: -0.012 }), holding({ security_id: "s2", ticker: "UP", day_change: 12.5, day_change_pct: 0.004 })]);
      const [down, up] = screen.getAllByRole("row").slice(1).map((r) => r.querySelector("[data-day-change]") as HTMLElement);
      expect(down).toHaveTextContent("Today −$30.00 (−1.20%)");
      expect(within(down).getByText("−$30.00")).toHaveClass("text-loss");
      expect(within(down).getByText("(−1.20%)")).toHaveClass("text-loss");
      expect(up).toHaveTextContent("Today +$12.50 (+0.40%)");
      expect(within(up).getByText("+$12.50")).toHaveClass("text-good");
      expect(within(up).getByText("(+0.40%)")).toHaveClass("text-good");
      expect(down.previousElementSibling).toHaveTextContent("10 × $250.00");   // its own line, not crammed into the quantity line
      expect(down.previousElementSibling).not.toHaveTextContent("Today");
    });

    it("shows the dollar change alone when the percent isn't known", () => {
      setup([holding({ day_change: 12.5, day_change_pct: null })]);
      expect(screen.getAllByRole("row")[1].querySelector("[data-day-change]")).toHaveTextContent(/^Today\s*\+\$12\.50$/);
    });

    it("leaves the day-change line off when the day's change isn't known", () => {
      setup([holding({ day_change: null, day_change_pct: null })]);
      expect(screen.getAllByRole("row")[1].querySelector("[data-day-change]")).toBeNull();
    });
  });

  describe("on a phone", () => {
    const phone = () => vi.stubGlobal("matchMedia", (q: string) => ({ matches: true, media: q, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
    beforeEach(phone);
    afterEach(() => vi.unstubAllGlobals());

    it("keeps total gain as its own centred column, not repeated under the value", () => {
      setup([holding({ value: 2500, gain: 500, gain_pct: 0.25 })]);
      const row = screen.getAllByRole("row")[1];
      const gainCell = within(row).getByText("+$500.00").closest("td")!;
      expect(gainCell).toHaveClass("text-center");
      expect(gainCell).not.toHaveClass("max-[1279px]:hidden");   // shown at phone and tablet widths, not clipped
      expect(within(row).getByText("$2,500.00").closest("td")).not.toHaveTextContent("+$500.00");
      expect(screen.getByRole("columnheader", { name: /Total gain/ })).toHaveClass("text-center");
    });

    it("shows Holding then Today in view, with Total gain and Value after them, as header and cells alike", () => {
      setup([holding({ value: 2500, gain: 500, gain_pct: 0.25, day_change: -30, day_change_pct: -0.012 })]);
      const heads = screen.getAllByRole("columnheader").map((h) => h.getAttribute("data-col"));
      const visible = heads.filter((h) => !["quantity", "price", "allocation", "cost_basis"].includes(h!));
      expect(visible).toEqual(["name", "day_change", "gain", "value"]);
      const row = screen.getAllByRole("row")[1];
      for (const [text, col] of [["−$30.00", "day_change"], ["$2,500.00", "value"], ["+$500.00", "gain"]] as const) {
        expect(within(row).getAllByText(text)).toHaveLength(1);
        expect(within(row).getByText(text).closest("td")!.cellIndex).toBe(heads.indexOf(col));
      }
      // Holding and Today fill the container exactly (Today is what's left of its width after the holding), and the table is
      // Total gain and Value (10.5rem) wider than the container, so only they need scrolling to.
      expect(screen.getByRole("table")).toHaveClass("phone:w-[calc(100cqw_+_10.5rem)]", "phone:table-fixed");
      expect(document.getElementById("inv-holdings")!.className).toMatch(/phone:\[container-type:inline-size\]/);
      expect(screen.getByRole("columnheader", { name: /Holding/ })).toHaveClass("phone:w-(--hold)");
      expect(screen.getByRole("columnheader", { name: /Today/ })).toHaveClass("phone:w-[calc(100cqw_-_var(--hold))]");
      expect(screen.getByRole("columnheader", { name: /Total gain/ })).toHaveClass("phone:w-20");
      expect(screen.getByRole("columnheader", { name: /Value/ })).toHaveClass("phone:w-22");
      expect(screen.getByRole("button", { name: /Value/ })).toBeInTheDocument();   // still sortable
    });

    it("shows Today's dollars over its percent, coloured, as a column that isn't hidden on a phone", () => {
      setup([holding({ day_change: -30, day_change_pct: -0.012 }), holding({ security_id: "s2", ticker: "UP", day_change: 12.5, day_change_pct: 0.004 })]);
      const [down, up] = screen.getAllByRole("row").slice(1);
      const cell = (r: HTMLElement, text: string) => within(r).getByText(text).closest("td")!;
      expect(cell(down, "−$30.00")).toHaveTextContent("−$30.00−1.20%");
      expect(within(down).getByText("−$30.00")).toHaveClass("text-loss");
      expect(within(down).getByText("−1.20%")).toHaveClass("text-loss");
      expect(within(up).getByText("+$12.50")).toHaveClass("text-good");
      expect(within(up).getByText("+0.40%")).toHaveClass("text-good");
      expect(cell(down, "−$30.00")).not.toHaveClass("max-[1279px]:hidden");
      expect(cell(down, "−$30.00").className).toMatch(/desktop:max-\[1279px\]:hidden/);   // hidden at tablet width only
      expect(screen.getByRole("columnheader", { name: /Today/ }).className).not.toMatch(/(^| )max-\[1279px\]:hidden/);
    });

    it("no longer repeats the day change under the holding, keeping its shares line compact", () => {
      setup([holding({ day_change: -30, day_change_pct: -0.012 })]);
      const row = screen.getAllByRole("row")[1];
      expect(row.querySelector("[data-day-change]")).toBeNull();
      expect(row.querySelector("td")).toHaveTextContent("10 × $250.00");
      expect(row.querySelector("td")).not.toHaveTextContent("Today");
    });

    it("shows the dollar change alone when the percent isn't known", () => {
      setup([holding({ day_change: 12.5, day_change_pct: null })]);
      const cell = within(screen.getAllByRole("row")[1]).getByText("+$12.50").closest("td")!;
      expect(cell).toHaveTextContent(/^\+\$12\.50$/);
    });

    it("shows a dash in Today when the day's change isn't known", () => {
      setup([holding({ day_change: null, day_change_pct: null })]);
      expect(within(screen.getAllByRole("row")[1]).getAllByText("—").length).toBeGreaterThanOrEqual(1);
    });

    it("keeps the holding in view while the table scrolls", () => {
      setup();
      expect(screen.getByRole("columnheader", { name: /Holding/ })).toHaveClass("phone:sticky", "phone:left-0", "phone:bg-card");
      expect(screen.getAllByRole("row")[1].querySelector("td")).toHaveClass("phone:sticky", "phone:left-0", "phone:bg-card");
    });

    it("sorts by gain from the Total gain column header", async () => {
      setup([holding({ ticker: "AAA", gain: 100 }), holding({ security_id: "s2", ticker: "BBB", gain: 900 })]);
      await userEvent.click(screen.getByRole("button", { name: /Total gain/ }));
      expect(inv.sort).toEqual({ key: "gain", dir: -1 });
    });

    it("shows a dash in the gain column when the cost basis is unknown", () => {
      setup([holding({ value: 2500, gain: null, gain_pct: null })]);
      expect(within(screen.getAllByRole("row")[1]).getByText("—", { selector: "td.text-center span" })).toBeInTheDocument();
    });
  });

  describe("sorting", () => {
    const three = [holding({ security_id: "1", name: "Bond", value: 100 }), holding({ security_id: "2", name: "Zed", value: 300 }), holding({ security_id: "3", name: "Apple", value: 200 })];

    it("starts with the biggest value first", () => {
      setup(three);
      expect(rowsText()).toEqual(["VTI Zed Brokerage", "VTI Apple Brokerage", "VTI Bond Brokerage"]);
      expect(screen.getByRole("columnheader", { name: /Value/ })).toHaveAttribute("aria-sort", "descending");
    });

    it("flips the order when the same column is clicked again", async () => {
      setup(three);
      await userEvent.click(screen.getByRole("button", { name: /Value/ }));
      expect(rowsText()[0]).toContain("Bond");
      expect(screen.getByRole("columnheader", { name: /Value/ })).toHaveAttribute("aria-sort", "ascending");
    });

    it("sorts names from A to Z first, and other columns biggest first", async () => {
      setup(three);
      await userEvent.click(screen.getByRole("button", { name: /Holding/ }));
      expect(rowsText().map((t) => t.split(" ")[1])).toEqual(["Apple", "Bond", "Zed"]);
    });

    it("puts holdings with no value for the column last when sorting biggest first", async () => {
      setup([holding({ security_id: "1", name: "A", gain: null }), holding({ security_id: "2", name: "B", gain: 5 })]);
      await userEvent.click(screen.getByRole("button", { name: /Total gain/ }));
      expect(rowsText()[0]).toContain("B");
    });
  });

  describe("cost basis editor", () => {
    const withLots = (lots = [lot()]) => holding({ lots, quantity: lots.reduce((n, l) => n + l.quantity, 0) });

    it("opens an input per account, prefilled only with a value you set yourself", async () => {
      setup([withLots([lot(), lot({ account_id: "acc2", account_name: "IRA", manual: true, per_share: 180.5 })])]);
      await userEvent.click(screen.getByRole("button", { name: /\$2,000\.00/ }));
      expect(screen.getByLabelText("Price per share in Brokerage")).toHaveValue(null);
      expect(screen.getByLabelText("Price per share in Brokerage")).toHaveAttribute("placeholder", "reported 200.00");
      expect(screen.getByLabelText("Price per share in IRA")).toHaveValue("180.5");
    });

    it("saves a price and closes when the security is in one account, then tells the page", async () => {
      const onchanged = setup([withLots()]);
      await userEvent.click(screen.getByRole("button", { name: /\$2,000\.00/ }));
      const input = screen.getByLabelText("Price per share in Brokerage");
      await userEvent.type(input, "210.5");
      await userEvent.tab();
      expect(api).toHaveBeenCalledWith("/api/investments/cost", { method: "POST", body: { account_id: "acc1", security_id: "s1", per_share: "210.5" } });
      expect(screen.queryByLabelText("Price per share in Brokerage")).not.toBeInTheDocument();
      expect(onchanged).toHaveBeenCalled();
    });

    it("previews the resulting cost basis as you type", async () => {
      setup([withLots()]);
      await userEvent.click(screen.getByRole("button", { name: /\$2,000\.00/ }));
      await userEvent.type(screen.getByLabelText("Price per share in Brokerage"), "100");
      expect(screen.getByText("= $1,000.00 cost basis")).toBeInTheDocument();
    });

    it("closes with Done and only reloads the page if something was saved", async () => {
      const onchanged = setup([withLots()]);
      await userEvent.click(screen.getByRole("button", { name: /\$2,000\.00/ }));
      await userEvent.click(screen.getByRole("button", { name: "Done" }));
      expect(onchanged).not.toHaveBeenCalled();
    });

    it("keeps the rows in place while editing, even if prices move them", async () => {
      const a = holding({ security_id: "1", name: "Alpha", value: 300, lots: [lot()] }), b = holding({ security_id: "2", name: "Beta", value: 200, lots: [lot({ security_id: "2" })] });
      const { rerender } = render(HoldingsTable, { holdings: [a, b], onchanged: vi.fn() });
      await userEvent.click(screen.getAllByTitle("Edit cost basis")[0]);
      await rerender({ holdings: [{ ...a, value: 100 }, b], onchanged: vi.fn() });
      expect(rowsText().slice(0, 2).map((t) => t.split(" ")[1])).toEqual(["Alpha", "Beta"]);
    });
  });
});
