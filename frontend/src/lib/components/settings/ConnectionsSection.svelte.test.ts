// @vitest-environment jsdom
import { render, screen, waitFor, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));
vi.mock("$lib/app.svelte", () => ({
  app: { state: { simplefin: false, last_log: null }, version: 0 },
  reload: vi.fn(), refreshState: vi.fn(),
}));
vi.mock("./plaid.svelte", () => ({
  plaidSession: { last: null }, connectPlaid: vi.fn(async () => {}), openPlaidLink: vi.fn(), matchPlaidAccount: vi.fn(),
}));

import { api } from "$lib/api";
import { app, reload } from "$lib/app.svelte";
import ConnectionsSection from "./ConnectionsSection.svelte";
import { connectPlaid } from "./plaid.svelte";
import type { PlaidItem, PlaidStatus, SettingsAccount } from "./types";

const item = (over: Partial<PlaidItem> = {}): PlaidItem => ({
  item_id: "it1", institution_name: "Chase", products: ["transactions"], bank: true, last_sync: "2026-09-30 15:00:00",
  accounts: [{ id: "pa1", name: "Freedom", type: "credit", account_id: null }], ...over });
const status = (items: PlaidItem[] = [], over: Partial<PlaidStatus> = {}): PlaidStatus => ({ configured: true, env: "production", client_id: "cid", items, last_inv_sync: null, syncing: false, inv_accounts: 0, simplefin_connected: false, simplefin_last_sync: null, simplefin_seen: [], ...over });
const serve = (st: PlaidStatus | Error) => vi.mocked(api).mockImplementation(async (path: string) => {
  if (path === "/api/plaid/status") { if (st instanceof Error) throw st; return st; }
  if (path === "/api/retail") throw new Error("The extension's status didn't load");   // its own tests cover it
  if (path.endsWith("/status")) return {};
  return { ok: true, new: 3 };
});
const acct = (id: string, org: string | null, provider = "simplefin"): SettingsAccount => ({ id, name: id, kind: "checking", org, provider });
const state = app.state as unknown as Record<string, unknown>;
// A service's row: the <details> whose summary carries its name.
const NAMES = "details > summary.min-h-14 > span > span.font-medium";
const card = (name: string) => [...document.querySelectorAll(NAMES)].find((e) => e.textContent === name)!.closest("details") as HTMLDetailsElement;
const badge = (name: string) => card(name).querySelector("summary [data-status]")!;

beforeEach(() => {
  vi.mocked(api).mockReset(); vi.mocked(reload).mockClear(); vi.mocked(connectPlaid).mockClear();
  state.simplefin = false; state.last_log = null; state.sync_warnings = []; state.connected = false;
  for (const k of ["has_api_key", "finnhub_configured", "realie_configured", "logodev_configured"]) delete state[k];
});

describe("Settings → Connections", () => {
  it("has a row per service, banks first: SimpleFIN and Plaid open until a bank is connected, each with its own way to connect", async () => {
    serve(status());
    render(ConnectionsSection);
    await screen.findByRole("button", { name: "Connect a bank or card" });
    expect([...document.querySelectorAll(NAMES)].map((e) => e.textContent)).toEqual(
      ["SimpleFIN", "Plaid", "Browser extension", "AI categorization", "Home values", "Live stock prices", "Merchant and bank logos"]);
    expect(card("SimpleFIN").open).toBe(true);
    expect(card("Plaid").open).toBe(true);
    expect(card("Browser extension").open).toBe(false);
    expect(within(card("SimpleFIN")).getByLabelText("SimpleFIN setup token")).toBeInTheDocument();
    expect(badge("SimpleFIN")).toHaveTextContent("Not set");
    expect(within(card("Plaid")).getByRole("button", { name: "Connect an investment account" })).toBeInTheDocument();
  });

  it("keeps the rows collapsed once a bank is connected, with where each one stands", async () => {
    state.connected = true; state.simplefin = true; state.last_log = { at: "2026-09-30T14:00:00+00:00", ok: true, message: "12 new" };
    serve(status([item(), item({ item_id: "it2", institution_name: "Fidelity" })]));
    render(ConnectionsSection);
    await waitFor(() => expect(badge("Plaid")).toHaveTextContent("2 connections"));
    expect(card("SimpleFIN").open).toBe(false);
    expect(card("Plaid").open).toBe(false);
    expect(badge("SimpleFIN")).toHaveTextContent("Connected");
  });

  it("shows SimpleFIN's last sync and which banks it brings in, with the accounts managed under Accounts", async () => {
    state.simplefin = true; state.last_log = { at: "2026-09-30T14:00:00+00:00", ok: true, message: "12 new" };
    serve(status());
    render(ConnectionsSection, { accounts: [acct("sf1", "Discover"), acct("sf2", "Ally Bank"), acct("sf3", "Ally Bank"), acct("pl:x", "Chase"),
      acct("sf4", "Amex", "plaid")] });   // switched to Plaid under Accounts: SimpleFIN no longer brings it in
    const sf = card("SimpleFIN");
    expect(badge("SimpleFIN")).toHaveTextContent("Connected");
    expect(within(sf).getByText(/^Last sync Sep 30, 10:00\sAM · 12 new$/)).toBeInTheDocument();   // in this browser's time zone
    expect(within(sf).getByText(/^3 accounts from Ally Bank, Discover ·/)).toBeInTheDocument();
    expect(within(sf).getByRole("link", { name: "Manage in Accounts" })).toHaveAttribute("href", "#setup/accounts");
    expect(within(sf).queryByLabelText("SimpleFIN setup token")).toBeNull();
    await userEvent.click(within(sf).getByRole("button", { name: "Replace the setup token" }));
    expect(within(sf).getByLabelText("SimpleFIN setup token")).toBeInTheDocument();
  });

  it("shows SimpleFIN in amber, with what was said, when the last sync failed or a bank needs attention", async () => {
    state.simplefin = true; state.last_log = { at: "2026-09-30 14:00:00", ok: false, message: "SimpleFIN is down" };
    serve(status());
    const { unmount } = render(ConnectionsSection);
    expect(await screen.findByText("SimpleFIN is down")).toHaveClass("text-amber-500");
    expect(badge("SimpleFIN")).toHaveTextContent("Needs attention");
    expect(card("SimpleFIN").open).toBe(true);
    expect(screen.getByText(/^Last sync Sep 30, 10:00\sAM$/)).toBeInTheDocument();   // the old UTC form reads as UTC
    unmount();
    state.last_log = { at: "2026-09-30T14:00:00+00:00", ok: true, message: "2 new transactions · bank messages: Chase: log in again" };
    state.sync_warnings = ["Chase: log in again"];
    render(ConnectionsSection);
    expect(await screen.findByText("Chase: log in again")).toHaveClass("text-amber-500");
    expect(screen.getByText(/^Last sync Sep 30, 10:00\sAM · 2 new transactions$/)).toBeInTheDocument();
  });

  it("connects through SimpleFIN with a setup token", async () => {
    serve(status());
    render(ConnectionsSection);
    const sf = card("SimpleFIN");
    await userEvent.type(within(sf).getByLabelText("SimpleFIN setup token"), "tok123");
    await userEvent.click(within(sf).getByRole("button", { name: "Connect and sync" }));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/connect", { method: "POST", body: { token: "tok123" } }));
    expect(api).toHaveBeenCalledWith("/api/sync", { method: "POST" });
    await waitFor(() => expect(reload).toHaveBeenCalled());
  });

  it("lists Plaid's connections in its row, with Sync, Remove and Reconnect, and opens it when one needs reconnecting", async () => {
    state.connected = true;
    serve(status([item(), item({ item_id: "it2", institution_name: "Fidelity", bank: false, products: ["investments"], accounts: [] }),
      item({ item_id: "it3", institution_name: "Amex", error: "ITEM_LOGIN_REQUIRED" })]));
    render(ConnectionsSection);
    expect(await screen.findByText("Chase")).toBeInTheDocument();
    const plaid = card("Plaid");
    expect(within(plaid).getByText("Fidelity")).toBeInTheDocument();
    expect(within(plaid).getByText("investments")).toBeInTheDocument();
    expect(within(plaid).queryByText("bank")).toBeNull();   // a plain bank needs no label
    expect(within(plaid).getAllByRole("button", { name: "Sync" })).toHaveLength(2);
    expect(within(plaid).getByRole("button", { name: "Reconnect" })).toBeInTheDocument();
    expect(within(plaid).getAllByRole("button", { name: "Remove" })).toHaveLength(3);
    expect(badge("Plaid")).toHaveTextContent("Needs attention");
    await waitFor(() => expect(plaid.open).toBe(true));
  });

  it("connects through Plaid as a bank or card, or as an investment account", async () => {
    serve(status());
    render(ConnectionsSection);
    await userEvent.click(await screen.findByRole("button", { name: "Connect a bank or card" }));
    await waitFor(() => expect(connectPlaid).toHaveBeenLastCalledWith("bank"));
    await userEvent.click(screen.getByRole("button", { name: "Connect an investment account" }));
    await waitFor(() => expect(connectPlaid).toHaveBeenLastCalledWith("investments"));
  });

  it("explains that Plaid needs API keys, with them open, when it isn't configured", async () => {
    serve(status([], { configured: false, client_id: "" }));
    render(ConnectionsSection);
    const keys = (await screen.findByText("Plaid API keys · not set")).closest("details")!;
    expect(keys.open).toBe(true);
    expect(screen.getByText(/Plaid needs API keys first/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Connect a bank or card" })).toBeNull();
    keys.open = false;
    keys.dispatchEvent(new Event("toggle"));
    await userEvent.click(await screen.findByRole("button", { name: "Add them" }));
    await waitFor(() => expect(keys.open).toBe(true));
    expect(connectPlaid).not.toHaveBeenCalled();
  });

  it("keeps the Plaid API keys collapsed once they're set, with every field", async () => {
    serve(status([item()]));
    render(ConnectionsSection);
    const summary = await screen.findByText("Plaid API keys · saved · Production");
    const keys = summary.closest("details")!;
    expect(keys.open).toBe(false);
    expect(within(keys).getByLabelText("Client ID")).toHaveValue("cid");
    expect(within(keys).getByLabelText("Environment")).toHaveValue("production");
    expect(within(keys).getByLabelText("Secret")).toHaveAttribute("type", "password");
  });
});

describe("Settings → Connections, the optional services", () => {
  it("are collapsed, and say On or Not set for each", async () => {
    state.connected = true; state.has_api_key = true; state.finnhub_configured = true;
    serve(status());
    render(ConnectionsSection);
    for (const name of ["AI categorization", "Home values", "Live stock prices", "Merchant and bank logos"]) expect(card(name).open).toBe(false);
    expect(badge("AI categorization")).toHaveTextContent("On");
    expect(badge("Live stock prices")).toHaveTextContent("On");
    expect(badge("Home values")).toHaveTextContent("Not set");
    expect(badge("Merchant and bank logos")).toHaveTextContent("Not set");
  });

  it("open to the key field, and never show a saved key", async () => {
    state.connected = true; state.realie_configured = true;
    serve(status());
    render(ConnectionsSection);
    expect(badge("Home values")).toHaveTextContent("On");
    await userEvent.click(within(card("Home values")).getByText("Home values"));
    expect(card("Home values").open).toBe(true);
    const key = within(card("Home values")).getByLabelText("Realie API key");
    expect(key).toHaveAttribute("type", "password");
    expect(key).toHaveValue("");
    expect(key).toHaveAttribute("placeholder", "•••••••• saved");
  });

  it("searches the web for card suggestions unless you switch it off", async () => {
    serve(status());
    render(ConnectionsSection);
    await screen.findByRole("button", { name: "Connect a bank or card" });
    const ai = within(card("AI categorization"));
    const web = ai.getByLabelText(/Search the web when filling in a card/);
    expect(web).toBeChecked();   // on unless switched off
    expect(ai.queryByText(/OpenRouter charges for the search/)).toBeNull();   // the docs cover the cost
    await userEvent.click(web);
    await waitFor(() => expect(vi.mocked(api)).toHaveBeenCalledWith("/api/settings", { method: "POST", body: { churn_ai_web: false } }));
  });
});
