// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));
const phone = vi.hoisted(() => ({ phone: false }));
vi.mock("$lib/phone.svelte", () => ({ viewport: phone, isPhone: () => phone.phone, PHONE_QUERY: "" }));
vi.mock("$lib/app.svelte", () => ({ app: { state: { brands: {}, connected: true }, version: 0 }, refreshState: vi.fn(), reload: vi.fn() }));

import { api } from "$lib/api";
import NetWorth from "../../../pages/NetWorth.svelte";

const calls = (path: string) => vi.mocked(api).mock.calls.filter((c) => c[0] === path);

const grant = {
  id: "g1", company_id: "c1", kind: "iso", label: "ES-1", granted_on: "2023-03-01", quantity: 40000, strike: 0.85, vest_start: "2023-03-01",
  vest_months: 48, cliff_months: 12, vest_every: 1, exercised: null, expires_on: "2033-03-01", vested: 20000, fully_vested_on: "2027-03-01",
  schedule: [], vested_value: 66000, unvested_value: 66000,
};
const equity = {
  companies: [{ id: "c1", name: "Acme Robotics", share_price: 4.2, price_as_of: "2026-09-01", in_networth: 1, source: "manual", grants: [grant], vested_value: 66000, unvested_value: 66000 }],
  vested_value: 66000, unvested_value: 66000, in_networth: 66000,
  carta: { env: "mock", client_id: null, has_secret: false, connected: true, last_sync: null, last_error: null, web_last: null, web_error: null, web_capture: false },
};
const nw = {
  today: "2026-09-30", net: 66000, assets: 66000, liabilities: 0, history: [], first_snapshot: null, change: { "30d": null, "90d": null, "1y": null },
  assets_list: [], loan_accounts: [], realie: { configured: false, used: 0, limit: 0 }, excluded: [],
  groups: [{ key: "equity", label: "Equity (vested)", side: "asset", total: 66000, items: [{ type: "equity", id: "c1", name: "Acme Robotics", value: 66000, as_of: "2026-09-01" }] }],
};

beforeEach(() => {
  phone.phone = false;
  vi.mocked(api).mockReset();
  vi.mocked(api).mockImplementation((async (path: string) => {
    if (path === "/api/networth") return nw;
    if (path === "/api/equity") return equity;
    return { ok: true };
  }) as never);
});

describe("Net worth summary", () => {
  it("has no Equity card, and its Equity (vested) group links to the equity view", async () => {
    render(NetWorth);
    const link = await screen.findByRole("link", { name: "Equity (vested)" });
    expect(link).toHaveAttribute("href", "#networth/equity");
    expect(screen.queryByRole("heading", { name: "Equity" })).not.toBeInTheDocument();
    expect(calls("/api/equity")).toHaveLength(0);
  });
});

describe("#networth/equity vesting chart", () => {
  const withSchedule = { ...grant, schedule: [["2026-01-01", 10000], ["2027-01-01", 20000], ["2028-01-01", 40000]] };
  const serve = () => vi.mocked(api).mockImplementation((async (path: string) => {
    if (path === "/api/networth") return nw;
    if (path === "/api/equity") return { ...equity, companies: [{ ...equity.companies[0], grants: [withSchedule] }] };
    return { ok: true };
  }) as never);
  afterEach(() => vi.useRealTimers());

  it("marks today on the chart, so it's clear what has vested", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(2026, 8, 30, 12));
    serve();
    render(NetWorth, { sub: "equity" });
    const line = await screen.findByTestId("mark-line");
    expect(screen.getByText("Today")).toBeInTheDocument();
    const x = Number(line.getAttribute("x1"));
    expect(x).toBeGreaterThan(56);
    expect(x).toBeLessThan(320 - 110 + 1);
  });

  it("leaves the marker off when today is past the last vesting date", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(2030, 0, 15));
    serve();
    render(NetWorth, { sub: "equity" });
    expect(await screen.findByText("Vesting over time")).toBeInTheDocument();
    expect(screen.queryByTestId("mark-line")).not.toBeInTheDocument();
  });
});

describe("#networth/equity", () => {
  it("says why a grant's vesting couldn't be worked out, instead of a silent zero", async () => {
    const broken = { ...grant, id: "g2", label: "ES-2", vested: 0, fully_vested_on: null, vested_value: 0, unvested_value: 0,
                     problem: "This grant’s vesting can’t be worked out: check its dates and vesting length." };
    vi.mocked(api).mockImplementation((async (path: string) => {
      if (path === "/api/networth") return nw;
      if (path === "/api/equity") return { ...equity, companies: [{ ...equity.companies[0], grants: [grant, broken] }] };
      return { ok: true };
    }) as never);
    render(NetWorth, { sub: "equity" });
    expect(await screen.findByText("This grant’s vesting can’t be worked out: check its dates and vesting length.")).toBeInTheDocument();
    expect(screen.getAllByText(/of 40,000/)).toHaveLength(2);   // both grants are still listed
  });

  it("shows vested and still-to-vest figures, the companies and the tab bar as the way back", async () => {
    render(NetWorth, { sub: "equity" });
    expect(await screen.findByRole("button", { name: "Add a company" })).toBeInTheDocument();   // the tab's toolbar; no second "Equity" heading
    expect(screen.queryByRole("heading", { name: "Equity" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Summary" })).toHaveAttribute("href", "#networth");
    expect(screen.getByText("Vested now")).toBeInTheDocument();
    expect(screen.getAllByText("Still to vest").length).toBeGreaterThan(0);
    expect(screen.getByText("Acme Robotics")).toBeInTheDocument();
    expect(screen.queryByText("How is this valued?")).not.toBeInTheDocument();
    expect(screen.queryByText(/Only what has vested counts/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sync from Carta" })).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("edits a grant in a side panel, then saves and closes it", async () => {
    render(NetWorth, { sub: "equity" });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Edit ES-1" }));
    const dialog = await screen.findByRole("dialog", { name: "Edit grant" });
    const shares = within(dialog).getByLabelText("Shares");
    expect(shares).toHaveValue(40000);
    await user.clear(shares);
    await user.type(shares, "50000");
    await user.click(within(dialog).getByRole("button", { name: "Save grant" }));
    await waitFor(() => expect(calls("/api/equity/grants/g1")).toHaveLength(1));
    expect((calls("/api/equity/grants/g1")[0][1] as { body: Record<string, string> }).body).toMatchObject({ kind: "iso", quantity: "50000", strike: "0.85" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(calls("/api/equity")).toHaveLength(2);
  });

  it("adds a grant to a company from the same panel, and cancel leaves nothing behind", async () => {
    render(NetWorth, { sub: "equity" });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Add a grant" }));
    let dialog = await screen.findByRole("dialog", { name: "Add a grant" });
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(calls("/api/equity/companies/c1/grants")).toHaveLength(0);
    // Bits UI hands the page its pointer events back once the panel has finished closing.
    await waitFor(() => expect(document.body.style.pointerEvents).not.toBe("none"));

    await user.click(screen.getByRole("button", { name: "Add a grant" }));
    dialog = await screen.findByRole("dialog", { name: "Add a grant" });
    await user.type(within(dialog).getByLabelText("Shares"), "1000");
    await user.click(within(dialog).getByRole("button", { name: "Add grant" }));
    await waitFor(() => expect(calls("/api/equity/companies/c1/grants")).toHaveLength(1));
    expect((calls("/api/equity/companies/c1/grants")[0][1] as { body: Record<string, string> }).body).toMatchObject({ quantity: "1000", kind: "iso" });
  });

  it("still saves the in-net-worth toggle", async () => {
    render(NetWorth, { sub: "equity" });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("checkbox", { name: /Count in net worth/ }));
    await waitFor(() => expect(calls("/api/equity/companies/c1")).toHaveLength(1));
    expect((calls("/api/equity/companies/c1")[0][1] as { body: unknown }).body).toEqual({ in_networth: false });
  });
});

describe("#networth/equity when it can't load", () => {
  it("says something went wrong with a way to try again, not bare red text", async () => {
    let fail = true;
    vi.mocked(api).mockImplementation((async (path: string) => {
      if (path === "/api/networth") return nw;
      if (path === "/api/equity") { if (fail) throw new Error("Server down"); return equity; }
      return { ok: true };
    }) as never);
    render(NetWorth, { sub: "equity" });
    expect(await screen.findByText("Something went wrong: Server down")).toBeInTheDocument();
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Vested now")).toBeInTheDocument();
  });
});

describe("#networth/equity's states", () => {
  const serve = (eq: object, over: (path: string) => unknown = () => undefined) => vi.mocked(api).mockImplementation((async (path: string) => {
    const r = over(path);
    if (r !== undefined) return r;
    if (path === "/api/networth") return nw;
    if (path === "/api/equity") return eq;
    return { ok: true };
  }) as never);
  afterEach(() => vi.useRealTimers());

  it("says what the figures are, under them", async () => {
    serve(equity);
    render(NetWorth, { sub: "equity" });
    expect(await screen.findByText("Vested value at the last price you entered, before tax")).toBeInTheDocument();
  });

  it("warns, in the warning color, when a share price is months old", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(2026, 8, 30, 12));
    serve({ ...equity, companies: [{ ...equity.companies[0], price_as_of: "2026-02-15" }] });
    const { unmount } = render(NetWorth, { sub: "equity" });
    const warn = await screen.findByTestId("stale-price");
    expect(warn).toHaveTextContent("price from 7 months ago");
    expect(warn).toHaveClass("text-warning");
    unmount();
    serve(equity);   // priced Sep 1: recent
    render(NetWorth, { sub: "equity" });
    await screen.findByText("Vested now");
    expect(screen.queryByTestId("stale-price")).toBeNull();
  });

  it("explains Carta's errors in a first line, with the way to Settings", async () => {
    serve({ ...equity, carta: { ...equity.carta, last_error: "Carta answered 500 for /portfolios", web_error: "Runway didn’t find any grants in what it read from Carta." } });
    render(NetWorth, { sub: "equity" });
    const problems = await screen.findAllByTestId("carta-problem");
    expect(problems).toHaveLength(2);
    expect(problems[0]).toHaveTextContent("Couldn’t sync from Carta · Fix in Settings");
    expect(problems[0]).toHaveTextContent("Carta answered 500 for /portfolios");
    expect(problems[1]).toHaveTextContent("Couldn’t read Carta through the browser extension");
    for (const p of problems) expect(within(p).getByRole("link", { name: "Fix in Settings" })).toHaveAttribute("href", "#setup/connections");
  });

  it("draws every company: past four, the smaller ones as Other", async () => {
    const co = (id: string, name: string, n: number) => ({ ...equity.companies[0], id, name, share_price: 1,
      grants: [{ ...grant, id: `g-${id}`, kind: "rsu", schedule: [["2026-01-01", n], ["2027-01-01", n * 2]] }] });
    serve({ ...equity, companies: [co("a", "Alpha", 100), co("b", "Beta", 400), co("c", "Gamma", 300), co("d", "Delta", 200), co("e", "Epsilon", 50)] });
    const { container } = render(NetWorth, { sub: "equity" });
    await screen.findByText("Vesting over time");
    const svg = container.querySelector("svg[role=slider]")!;
    expect(svg.textContent).toContain("Other");
    expect(svg.textContent).not.toContain("Epsilon");
    expect(svg.textContent).not.toContain("Alpha");
    expect(svg.textContent).toContain("Beta");
  });

  it("keeps the figures when loading them again fails, under a Couldn't refresh line", async () => {
    let fail = false;
    serve(equity, (path) => { if (path === "/api/equity" && fail) throw new Error("Server down"); });
    render(NetWorth, { sub: "equity" });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("checkbox", { name: /Count in net worth/ }));   // saved; the reload after it fails
    fail = true;
    await user.click(screen.getByRole("checkbox", { name: /Count in net worth/ }));
    expect(await screen.findByTestId("refresh-failed")).toHaveTextContent("Couldn’t refresh");
    expect(screen.getByText("Vested now")).toBeInTheDocument();
    expect(screen.getByText("Acme Robotics")).toBeInTheDocument();
    fail = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.queryByTestId("refresh-failed")).toBeNull());
  });

  it("adds a company with Enter, once, and says what's missing", async () => {
    let finish: () => void = () => {};
    serve(equity, (path) => path === "/api/equity/companies" ? new Promise((r) => { finish = () => r({ ok: true }); }) : undefined);
    render(NetWorth, { sub: "equity" });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Add a company" }));
    const name = screen.getByPlaceholderText("Acme, Inc.");
    await user.type(name, "{Enter}");
    expect(screen.getByRole("alert")).toHaveTextContent("Give the company a name");
    expect(calls("/api/equity/companies")).toHaveLength(0);
    await user.type(name, "Initech{Enter}");
    expect(screen.getByRole("button", { name: "Adding…" })).toBeDisabled();
    await user.type(name, "{Enter}");   // a second Enter while it saves doesn't add it twice
    expect(calls("/api/equity/companies")).toHaveLength(1);
    expect((calls("/api/equity/companies")[0][1] as { body: unknown }).body).toEqual({ name: "Initech", share_price: "" });
    finish();
    await waitFor(() => expect(screen.queryByPlaceholderText("Acme, Inc.")).toBeNull());
  });

  it("previews the vesting in a line as you fill in the grant, and wants the shares", async () => {
    serve(equity, (path) => path === "/api/equity/companies/c1/grants" ? Promise.reject(new Error("Shares must be a number")) : undefined);
    render(NetWorth, { sub: "equity" });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Add a grant" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a grant" });
    expect(within(dialog).queryByTestId("vesting-preview")).toBeNull();   // no start date yet
    await fireEvent.input(within(dialog).getByLabelText("Vesting starts"), { target: { value: "2026-01-15" } });
    expect(within(dialog).getByTestId("vesting-preview").textContent!.replace(/\u00a0/g, " ")).toBe("25% on Jan 2027, then monthly until Jan 2030");
    await user.selectOptions(within(dialog).getByLabelText("Every"), "quarter");
    expect(within(dialog).getByTestId("vesting-preview")).toHaveTextContent(/then quarterly/);

    await user.click(within(dialog).getByRole("button", { name: "Add grant" }));
    expect(within(dialog).getByText("Enter how many shares")).toBeInTheDocument();
    expect(calls("/api/equity/companies/c1/grants")).toHaveLength(0);
    await user.type(within(dialog).getByLabelText("Shares"), "1000");
    await user.click(within(dialog).getByRole("button", { name: "Add grant" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Shares must be a number");   // refused: stays open, says why
    expect(screen.getByRole("dialog", { name: "Add a grant" })).toBeInTheDocument();
  });

  it("on a phone, shows each grant as a card with every figure, and actions to tap", async () => {
    phone.phone = true;
    serve(equity);
    render(NetWorth, { sub: "equity" });
    const cards = await screen.findByTestId("grant-cards");
    expect(screen.queryByRole("table")).toBeNull();
    const card = within(cards).getAllByRole("listitem")[0];
    for (const label of ["Granted", "Shares", "Strike", "Still to vest"]) expect(within(card).getByText(label)).toBeInTheDocument();
    expect(card).toHaveTextContent("$0.85");
    expect(within(card).getByRole("button", { name: "Edit ES-1" })).toHaveClass("min-h-11");
  });
});
