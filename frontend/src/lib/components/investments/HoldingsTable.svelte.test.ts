// @vitest-environment jsdom
import { render, screen, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { tick } from "svelte";
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

beforeEach(() => { inv.sort = { key: "value", dir: -1 }; inv.holdView = "day"; inv.listSort = { by: "value", dir: -1 }; vi.mocked(api).mockClear(); });

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

  // The same order at every width: Holding, Today, Total gain, Value, then Shares, Price, Weight, Cost basis (hidden under 1280px).
  const ORDER = ["name", "day_change", "gain", "value", "quantity", "price", "allocation", "cost_basis"];
  const stub = (matches: (q: string) => boolean) => vi.stubGlobal("matchMedia", (q: string) => ({ matches: matches(q), media: q, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  const heads = () => screen.getAllByRole("columnheader").map((h) => h.getAttribute("data-col"));
  const cellOf = (row: HTMLElement, text: string) => within(row).getByText(text).closest("td") as HTMLTableCellElement;
  const one = () => holding({ value: 2500, gain: 500, gain_pct: 0.25, day_change: -30, day_change_pct: -0.012 });

  const everyWidth: [string, (q: string) => boolean][] = [
    ["a wide screen (1280px and up)", () => false],
    ["a tablet (768px to 1279px)", (q) => q === "(max-width: 1279px)"],
  ];
  describe.each(everyWidth)("on %s", (_name, matches) => {
    beforeEach(() => stub(matches));
    afterEach(() => vi.unstubAllGlobals());

    it("orders the columns Holding, Today, Total gain, Value, then the rest, header and cells alike", () => {
      setup([one()]);
      expect(heads()).toEqual(ORDER);
      const row = screen.getAllByRole("row")[1];
      for (const [text, col] of [["−$30.00", "day_change"], ["+$500.00", "gain"], ["$2,500.00", "value"]] as const) {
        expect(within(row).getAllByText(text)).toHaveLength(1);
        expect(cellOf(row, text).cellIndex).toBe(ORDER.indexOf(col));
      }
    });

    it("has Today as a column, coloured, dollars over percent, and not as a line under the holding", () => {
      setup([one(), holding({ security_id: "s2", ticker: "UP", day_change: 12.5, day_change_pct: 0.004 })]);
      const [down, up] = screen.getAllByRole("row").slice(1);
      expect(cellOf(down, "−$30.00")).toHaveTextContent("−$30.00−1.20%");
      expect(within(down).getByText("−$30.00")).toHaveClass("text-loss");
      expect(within(down).getByText("−1.20%")).toHaveClass("text-loss");
      expect(within(up).getByText("+$12.50")).toHaveClass("text-good");
      expect(within(up).getByText("+0.40%")).toHaveClass("text-good");
      expect(cellOf(down, "−$30.00").className).not.toMatch(/hidden/);
      expect(screen.getByRole("columnheader", { name: /Today/ }).className).not.toMatch(/hidden/);
      expect(down.querySelector("td")).not.toHaveTextContent("Today");
      expect(down.querySelector("[data-day-change]")).toBeNull();
    });

    it("shows the dollar change alone when the percent isn't known, and a dash when the change isn't", () => {
      setup([holding({ day_change: 12.5, day_change_pct: null }), holding({ security_id: "s2", ticker: "NONE", day_change: null, day_change_pct: null })]);
      const [known, unknown] = screen.getAllByRole("row").slice(1);
      expect(cellOf(known, "+$12.50")).toHaveTextContent(/^\+\$12\.50$/);
      expect((unknown.querySelectorAll("td")[1] as HTMLElement)).toHaveTextContent("—");
    });

    it("keeps total gain a centred column, not repeated under the value", () => {
      setup([one()]);
      const row = screen.getAllByRole("row")[1];
      expect(cellOf(row, "+$500.00")).toHaveClass("text-center");
      expect(cellOf(row, "$2,500.00")).not.toHaveTextContent("+$500.00");
      expect(screen.getByRole("columnheader", { name: /Total gain/ })).toHaveClass("text-center");
    });

    it("sorts from every column header, Today and Total gain included", async () => {
      setup([holding({ ticker: "AAA", gain: 100, day_change: 5 }), holding({ security_id: "s2", ticker: "BBB", gain: 900, day_change: 1 })]);
      await userEvent.click(screen.getByRole("button", { name: /Total gain/ }));
      expect(inv.sort).toEqual({ key: "gain", dir: -1 });
      await userEvent.click(screen.getByRole("button", { name: /Today/ }));
      expect(inv.sort).toEqual({ key: "day_change", dir: -1 });
      expect(screen.getByRole("columnheader", { name: /Today/ })).toHaveAttribute("aria-sort", "descending");
    });
  });

  describe("on a wide screen (1280px and up)", () => {
    beforeEach(() => stub(() => false));
    afterEach(() => vi.unstubAllGlobals());

    it("shows every column, with cost basis in its own last cell", () => {
      setup([one()]);
      expect(screen.getAllByRole("columnheader")).toHaveLength(8);
      const costCell = within(screen.getAllByRole("row")[1]).getByTitle("Edit cost basis").closest("td") as HTMLTableCellElement;
      expect(costCell.cellIndex).toBe(7);   // its own last column, not folded into the holding cell
    });
  });

  describe("on a tablet (768px to 1279px)", () => {
    beforeEach(() => stub((q) => q === "(max-width: 1279px)"));
    afterEach(() => vi.unstubAllGlobals());

    it("folds shares, price and weight into the holding's cell, and hides those columns", () => {
      setup([one()]);
      expect(screen.getAllByRole("row")[1].querySelector("td")).toHaveTextContent("10 × $250.00");
      for (const k of ["quantity", "price", "allocation", "cost_basis"]) expect(document.querySelector(`th[data-col=${k}]`)).toHaveClass("max-[1279px]:hidden");
      for (const k of ["day_change", "gain", "value"]) expect(document.querySelector(`th[data-col=${k}]`)!.className).not.toMatch(/hidden/);
    });
  });

  describe("on a phone", () => {
    const PHONE = "(max-width: 767px), (max-height: 500px) and (pointer: coarse)";
    beforeEach(() => stub((q) => q === PHONE || q === "(max-width: 1279px)"));
    afterEach(() => vi.unstubAllGlobals());
    const three = () => [
      holding({ security_id: "1", ticker: "AAA", name: "Alpha", value: 300, allocation: 0.3, day_change_pct: -0.02, gain_pct: 0.1, gain: 10 }),
      holding({ security_id: "2", ticker: "BBB", name: "Beta", value: 200, allocation: 0.5, day_change_pct: 0.03, gain_pct: -0.2, gain: -10 }),
      holding({ security_id: "3", ticker: "CCC", name: "Gamma", value: 100, allocation: 0.2, day_change_pct: 0, gain_pct: null, gain: null, cost_known: false }),
    ];
    const items = () => screen.getAllByRole("listitem");
    const order = () => items().map((li) => li.querySelector("b")!.textContent);
    const pillsOf = () => items().map((li) => li.querySelector("span.rounded-full")!.textContent);

    it("renders a list, not a table, with the ticker, name, value and Today's pill", () => {
      setup(three());
      expect(screen.queryByRole("table")).not.toBeInTheDocument();
      expect(order()).toEqual(["AAA", "BBB", "CCC"]);
      expect(items()[0]).toHaveTextContent("Alpha");
      expect(items()[0]).toHaveTextContent("$300.00");
      expect(pillsOf()).toEqual(["−2.00%", "+3.00%", "0.00%"]);
      expect(within(items()[0]).getByText("−2.00%")).toHaveClass("bg-loss/15", "text-loss");
      expect(within(items()[1]).getByText("+3.00%")).toHaveClass("bg-good/15", "text-good");
      expect(within(items()[2]).getByText("0.00%")).toHaveClass("text-muted-foreground");
    });

    it("changes the pill's figure with the switch, and remembers the choice", async () => {
      setup(three());
      await userEvent.click(screen.getByRole("button", { name: "Total" }));
      expect(pillsOf()).toEqual(["+10.0%", "−20.0%", "No cost basis"]);
      expect(inv.holdView).toBe("total");
      await userEvent.click(screen.getByRole("button", { name: "Weight" }));
      expect(pillsOf()).toEqual(["30%", "50%", "20%"]);
      expect(inv.holdView).toBe("weight");
      expect(order()).toEqual(["AAA", "BBB", "CCC"]);   // still by value
    });

    it("shows 'No cost basis' under Total for a holding without one, and the gain on the second line otherwise", async () => {
      setup(three());
      await userEvent.click(screen.getByRole("button", { name: "Total" }));
      expect(within(items()[2]).getByText("No cost basis")).toHaveClass("text-muted-foreground");
      expect(items()[0]).toHaveTextContent("Alpha · +10.0%");
    });

    it("sorts by the figure on a second tap of the active choice, descending then ascending", async () => {
      setup(three());
      await userEvent.click(screen.getByRole("button", { name: "Weight" }));
      await userEvent.click(screen.getByRole("button", { name: /Weight/ }));
      expect(order()).toEqual(["BBB", "AAA", "CCC"]);
      await userEvent.click(screen.getByRole("button", { name: /Weight/ }));
      expect(order()).toEqual(["CCC", "AAA", "BBB"]);
      await userEvent.click(screen.getByRole("button", { name: "Total" }));   // another choice: back to value
      expect(order()).toEqual(["AAA", "BBB", "CCC"]);
    });

    it("puts a holding with no figure last, whichever way the list runs", async () => {
      setup(three());
      await userEvent.click(screen.getByRole("button", { name: "Total" }));
      await userEvent.click(screen.getByRole("button", { name: /Total/ }));
      expect(order()).toEqual(["AAA", "BBB", "CCC"]);
      await userEvent.click(screen.getByRole("button", { name: /Total/ }));
      expect(order()).toEqual(["BBB", "AAA", "CCC"]);
    });

    it("opens the cost basis editor from a row, and closes it with Done", async () => {
      setup([holding({ lots: [lot()] })]);
      await userEvent.click(screen.getByRole("button", { name: /VTI/ }));
      expect(screen.getByLabelText("Price per share in Brokerage")).toBeInTheDocument();
      await userEvent.click(screen.getByRole("button", { name: "Done" }));
      expect(screen.queryByLabelText("Price per share in Brokerage")).not.toBeInTheDocument();
    });

    // A window resized (or a phone turned) while the page is open: the media query changes under the component.
    function liveStub() {
      const mqs: { query: string; matches: boolean; fns: (() => void)[] }[] = [];
      vi.stubGlobal("matchMedia", (query: string) => {
        const mq = { query, matches: query === PHONE || query === "(max-width: 1279px)", fns: [] as (() => void)[],
          addEventListener: (_: string, f: () => void) => mq.fns.push(f), removeEventListener: vi.fn() };
        mqs.push(mq);
        return mq;
      });
      return async (phone: boolean) => {
        for (const mq of mqs) if (mq.query === PHONE) { mq.matches = phone; mq.fns.forEach((f) => f()); }
        await tick();
      };
    }

    it("switches between the list and the table when the screen changes while the page is open", async () => {
      const setPhone = liveStub();
      setup(three());
      expect(screen.queryByRole("table")).not.toBeInTheDocument();
      await setPhone(false);
      expect(screen.getByRole("table")).toBeInTheDocument();
      expect(screen.queryAllByRole("listitem")).toHaveLength(0);
      await setPhone(true);
      expect(screen.queryByRole("table")).not.toBeInTheDocument();
      expect(items()).toHaveLength(3);
    });

    it("keeps an open cost basis editor across the switch", async () => {
      const setPhone = liveStub();
      setup([holding({ lots: [lot()] })]);
      await userEvent.click(screen.getByRole("button", { name: /VTI/ }));
      expect(screen.getByLabelText("Price per share in Brokerage")).toBeInTheDocument();
      await setPhone(false);
      expect(screen.getByRole("table")).toBeInTheDocument();
      expect(screen.getByLabelText("Price per share in Brokerage")).toBeInTheDocument();
      await setPhone(true);
      expect(screen.getByLabelText("Price per share in Brokerage")).toBeInTheDocument();
    });

    it("keeps cash a plain row, without an editor", () => {
      setup([holding({ is_cash: true, ticker: null, name: "Cash", gain: null, gain_pct: null })]);
      expect(screen.queryByTitle("Edit cost basis")).not.toBeInTheDocument();
      expect(items()[0]).toHaveTextContent("Cash");
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
