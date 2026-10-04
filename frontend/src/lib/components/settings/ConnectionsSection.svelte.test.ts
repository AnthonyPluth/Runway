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
import { app, refreshState, reload } from "$lib/app.svelte";
import { toast } from "svelte-sonner";
import ConnectionsSection from "./ConnectionsSection.svelte";
import { connectPlaid } from "./plaid.svelte";
import type { PlaidItem, PlaidStatus, SettingsAccount } from "./types";

const item = (over: Partial<PlaidItem> = {}): PlaidItem => ({
  item_id: "it1", institution_name: "Chase", products: ["transactions"], bank: true, last_sync: "2026-09-30 15:00:00",
  accounts: [{ id: "pa1", name: "Freedom", type: "credit", account_id: null }], ...over });
const status = (items: PlaidItem[] = [], over: Partial<PlaidStatus> = {}): PlaidStatus => ({ configured: true, env: "production", client_id: "cid", items, last_inv_sync: null, syncing: false, inv_accounts: 0, simplefin_connected: false, simplefin_last_sync: null, simplefin_seen: [], ...over });
let statuses: Record<string, unknown> = {};
const serve = (st: PlaidStatus | Error) => vi.mocked(api).mockImplementation(async (path: string) => {
  if (path in statuses) return statuses[path];
  if (path === "/api/plaid/status") { if (st instanceof Error) throw st; return st; }
  if (path === "/api/retail") throw new Error("The extension's status didn't load");
  if (path.endsWith("/status")) return statuses[path] ?? {};
  return { ok: true, new: 3 };
});
const acct = (id: string, org: string | null, provider = "simplefin"): SettingsAccount => ({ id, name: id, kind: "checking", org, provider });
const state = app.state as unknown as Record<string, unknown>;
const NAMES = "details > summary [data-service]";
const card = (name: string) => [...document.querySelectorAll(NAMES)].find((e) => e.textContent === name)!.closest("details") as HTMLDetailsElement;
const badge = (name: string) => card(name).querySelector("summary [data-status]")!;

beforeEach(() => {
  vi.mocked(api).mockReset(); vi.mocked(reload).mockClear(); vi.mocked(connectPlaid).mockClear(); vi.mocked(toast.error).mockClear();
  vi.mocked(refreshState).mockReset();
  state.simplefin = false; state.last_log = null; state.sync_warnings = []; state.connected = false; statuses = {};
  for (const k of ["has_api_key", "finnhub_configured", "realie_configured", "logodev_configured", "last_llm_error", "llm_model",
    "llm_model_default", "card_ai_model", "card_ai_model_default"]) delete state[k];
});

describe("Settings → Connections", () => {
  it("has a row per service, banks first: before a bank, SimpleFIN and Plaid side by side, collapsed, SimpleFIN recommended", async () => {
    serve(status([], { configured: false, client_id: "" }));
    render(ConnectionsSection);
    await screen.findByText(/Plaid needs API keys first/);
    expect([...document.querySelectorAll(NAMES)].map((e) => e.textContent)).toEqual(
      ["SimpleFIN", "Plaid", "Browser extension", "AI categorization", "Home values", "Live stock prices", "Merchant and bank logos"]);
    expect(card("SimpleFIN").open).toBe(false);
    expect(card("Plaid").open).toBe(false);
    expect(within(card("SimpleFIN").querySelector("summary")!).getByText("Recommended")).toBeInTheDocument();
    expect(within(card("SimpleFIN").querySelector("summary")!).getByText("Easiest · about $15 a year")).toBeInTheDocument();
    expect(within(card("Plaid").querySelector("summary")!).queryByText("Recommended")).toBeNull();
    expect(within(card("Plaid").querySelector("summary")!).getByText("Needs your own Plaid developer keys")).toBeInTheDocument();
    expect(badge("SimpleFIN")).toHaveTextContent("Not set");
    expect(badge("Plaid")).toHaveTextContent("Not set");
    expect(within(card("SimpleFIN")).getByLabelText("SimpleFIN setup token")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Banks" }).nextElementSibling).toBe(card("SimpleFIN"));
  });

  it("opens one way to add a bank at a time", async () => {
    serve(status());
    render(ConnectionsSection);
    await screen.findByRole("button", { name: "Connect a bank or card" });
    await userEvent.click(within(card("SimpleFIN")).getByText("SimpleFIN"));
    expect(card("SimpleFIN").open).toBe(true);
    await userEvent.click(within(card("Plaid")).getByText("Plaid"));
    await waitFor(() => expect(card("SimpleFIN").open).toBe(false));
    expect(card("Plaid").open).toBe(true);
    await userEvent.click(within(card("AI categorization")).getByText("AI categorization"));
    expect(card("Plaid").open).toBe(true);
  });

  it("drops the Recommended tag once a bank is connected, and says Plaid's keys are saved before its first bank", async () => {
    state.connected = true; state.simplefin = true;
    serve(status([]));
    render(ConnectionsSection);
    await waitFor(() => expect(badge("Plaid")).toHaveTextContent("Keys saved"));
    expect(badge("Plaid")).toHaveAttribute("data-status", "off");
    expect(within(card("SimpleFIN")).queryByText("Recommended")).toBeNull();
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
      acct("sf4", "Amex", "plaid")] });
    const sf = card("SimpleFIN");
    expect(badge("SimpleFIN")).toHaveTextContent("Connected");
    expect(within(sf).getByText(/^Last sync Sep 30, 10:00\sAM · 12 new$/)).toBeInTheDocument();
    expect(within(sf).getByText(/^3 accounts from Ally Bank, Discover ·/)).toBeInTheDocument();
    expect(within(sf).getByRole("link", { name: "Manage in Accounts" })).toHaveAttribute("href", "#setup/accounts");
    expect(within(sf).queryByLabelText("SimpleFIN setup token")).toBeNull();
    await userEvent.click(within(sf).getByRole("button", { name: "Replace the setup token" }));
    expect(within(sf).getByLabelText("SimpleFIN setup token")).toBeInTheDocument();
  });

  it("says plainly when the last sync failed, with SimpleFIN's words under Details, and shows a bank's message as it is", async () => {
    state.simplefin = true; state.last_log = { at: "2026-09-30 14:00:00", ok: false, message: "HTTP 502 from bridge" };
    serve(status());
    const { unmount } = render(ConnectionsSection);
    expect(await screen.findByText(/The last sync didn’t finish/)).toHaveClass("text-warning");
    const raw = screen.getByText("HTTP 502 from bridge");
    expect(raw.closest("details")).not.toHaveAttribute("open");
    expect(within(raw.closest("details")!).getByText("Details")).toBeInTheDocument();
    expect(badge("SimpleFIN")).toHaveTextContent("Needs attention");
    expect(badge("SimpleFIN")).toHaveClass("text-warning");
    expect(card("SimpleFIN").open).toBe(true);
    expect(screen.getByText(/^Last sync Sep 30, 10:00\sAM$/)).toBeInTheDocument();
    unmount();
    state.last_log = { at: "2026-09-30T14:00:00+00:00", ok: true, message: "2 new transactions · bank messages: Chase: log in again" };
    state.sync_warnings = ["Chase: log in again"];
    render(ConnectionsSection);
    expect(await screen.findByText("Chase: log in again")).toHaveClass("text-warning");
    expect(screen.getByText(/^Last sync Sep 30, 10:00\sAM · 2 new transactions$/)).toBeInTheDocument();
  });

  it("connects through SimpleFIN with a setup token, then shows the accounts it brought in", async () => {
    location.hash = "#setup/connections";
    serve(status());
    render(ConnectionsSection);
    const sf = card("SimpleFIN");
    await userEvent.type(within(sf).getByLabelText("SimpleFIN setup token"), "tok123");
    await userEvent.click(within(sf).getByRole("button", { name: "Connect and sync" }));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/connect", { method: "POST", body: { token: "tok123" } }));
    expect(api).toHaveBeenCalledWith("/api/sync", { method: "POST" });
    await waitFor(() => expect(location.hash).toBe("#setup/accounts"));
    expect(toast.success).toHaveBeenCalledWith("Connected · 3 transactions imported");
  });

  it("stays on Connections when the setup token is replaced", async () => {
    location.hash = "#setup/connections";
    state.connected = true; state.simplefin = true;
    serve(status());
    render(ConnectionsSection);
    const sf = card("SimpleFIN");
    await userEvent.click(within(sf).getByRole("button", { name: "Replace the setup token" }));
    await userEvent.type(within(sf).getByLabelText("SimpleFIN setup token"), "tok456");
    await userEvent.click(within(sf).getByRole("button", { name: "Connect and sync" }));
    await waitFor(() => expect(reload).toHaveBeenCalled());
    expect(location.hash).toBe("#setup/connections");
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
    expect(within(plaid).queryByText("bank")).toBeNull();
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
    const summary = await screen.findByText("Plaid API keys · Keys saved · Production");
    const keys = summary.closest("details")!;
    expect(keys.open).toBe(false);
    expect(within(keys).getByLabelText("Client ID")).toHaveValue("cid");
    expect(within(keys).getByLabelText("Environment")).toHaveValue("production");
    const secret = within(keys).getByLabelText("Secret");
    expect(secret).toHaveAttribute("type", "password");
    expect(secret).toHaveAttribute("autocomplete", "new-password");
    expect(secret).toHaveAttribute("placeholder", "Key saved");
    await userEvent.click(within(keys).getByRole("button", { name: "Show Secret" }));
    expect(secret).toHaveAttribute("type", "text");
    await userEvent.click(within(keys).getByRole("button", { name: "Hide Secret" }));
    expect(secret).toHaveAttribute("type", "password");
  });

  it("saves Plaid's keys together with Save, not field by field, and empties the secret once saved", async () => {
    serve(status([], { configured: false, client_id: "" }));
    render(ConnectionsSection);
    const keys = (await screen.findByText("Plaid API keys · not set")).closest("details")!;
    const save = within(keys).getByRole("button", { name: "Save" });
    expect(save).toBeDisabled();
    await userEvent.type(within(keys).getByLabelText("Client ID"), "abc");
    await userEvent.tab();
    expect(api).not.toHaveBeenCalledWith("/api/plaid/settings", expect.anything());
    await userEvent.click(save);
    expect(toast.error).toHaveBeenCalledWith("Enter the secret.");
    expect(api).not.toHaveBeenCalledWith("/api/plaid/settings", expect.anything());
    const secret = within(keys).getByLabelText("Secret");
    await userEvent.type(secret, "s3cret");
    await userEvent.selectOptions(within(keys).getByLabelText("Environment"), "sandbox");
    await userEvent.click(save);
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/plaid/settings",
      { method: "POST", body: { env: "sandbox", client_id: "abc", secret: "s3cret" } }));
    await waitFor(() => expect(secret).toHaveValue(""));
    expect(reload).toHaveBeenCalled();
  });

  it("keeps Plaid Link's session IDs behind Details", async () => {
    const { plaidSession } = await import("./plaid.svelte");
    plaidSession.last = { sid: "sess-123", request: "req-9", at: "Oct 1" };
    serve(status());
    render(ConnectionsSection);
    const sid = await screen.findByText("sess-123");
    expect(sid.closest("details")).not.toHaveAttribute("open");
    expect(within(sid.closest("details")!).getByText("Details of the last Plaid Link attempt")).toBeInTheDocument();
    plaidSession.last = null;
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
    expect(key).toHaveAttribute("autocomplete", "new-password");
    expect(key).toHaveValue("");
    expect(key).toHaveAttribute("placeholder", "Key saved");
  });

  it("empties each key field once its key is saved", async () => {
    state.connected = true;
    serve(status());
    render(ConnectionsSection);
    for (const [row, label, path] of [["AI categorization", "OpenRouter API key", "/api/settings"], ["Home values", "Realie API key", "/api/realie/settings"],
      ["Live stock prices", "Finnhub API key", "/api/finnhub/settings"], ["Merchant and bank logos", "Publishable key", "/api/logodev/settings"]]) {
      const key = within(card(row)).getByLabelText(label);
      await userEvent.type(key, "k-123");
      await userEvent.tab();
      await waitFor(() => expect(api).toHaveBeenCalledWith(path, expect.objectContaining({ method: "POST" })));
      await waitFor(() => expect(key).toHaveValue(""));
    }
  });

  it("need attention when they hold an error, opening with it said plainly and the service's words under Details", async () => {
    state.connected = true; state.has_api_key = true; state.finnhub_configured = true; state.logodev_configured = true;
    state.last_llm_error = "401 Unauthorized from upstream";
    statuses = { "/api/finnhub/status": { configured: true, active: true, connected: false, symbols: 0, limit: 50, error: "socket closed 1006" },
      "/api/logodev/status": { plaid: 0, logodev: 2, unknown: 0, waiting: 0, last_error: "HTTP 403", last_error_name: null, searchable: false, fetching: false } };
    serve(status());
    render(ConnectionsSection);
    for (const name of ["AI categorization", "Live stock prices", "Merchant and bank logos"]) {
      await waitFor(() => expect(badge(name)).toHaveTextContent("Needs attention"));
      expect(badge(name)).toHaveAttribute("data-status", "warn");
      expect(card(name).open).toBe(true);
    }
    expect(within(card("AI categorization")).getByText("The last AI request failed.")).toBeInTheDocument();
    expect(within(card("AI categorization")).getByText("401 Unauthorized from upstream").closest("details")).not.toHaveAttribute("open");
    expect(within(card("Live stock prices")).getByText("socket closed 1006").closest("details")).toBeTruthy();
    expect(within(card("Merchant and bank logos")).getByText(/By website: HTTP 403/).closest("details")).toBeTruthy();
    expect(badge("Home values")).toHaveTextContent("Not set");
  });

  it("shows the browser extension's row needing attention when Carta's last read went wrong", async () => {
    state.connected = true;
    const store = { name: "Store", orders: 0, read: 0, matched: 0, unmatched: 0 };
    statuses = { "/api/equity": { carta: { env: "production", has_secret: false, connected: false, web_error: "Carta changed its page" } },
      "/api/retail": { token: true, ai: false, stores: { amazon: store, target: store, costco: store }, recent: [] } };
    serve(status());
    render(ConnectionsSection);
    await waitFor(() => expect(badge("Browser extension")).toHaveTextContent("Needs attention"));
    expect(card("Browser extension").open).toBe(true);
    expect(within(card("Browser extension")).getByText("The extension couldn’t read Carta last time.")).toBeInTheDocument();
    expect(within(card("Browser extension")).getByText("Carta changed its page").closest("details")).not.toHaveAttribute("open");
  });

  it("say what the AI is sent, link to a key, and show the default models as placeholders", async () => {
    state.connected = true; state.has_api_key = true;
    Object.assign(state, { llm_model: "vendor/default-a", llm_model_default: "vendor/default-a", card_ai_model: "vendor/mine", card_ai_model_default: "vendor/default-b" });
    serve(status());
    render(ConnectionsSection);
    const ai = within(card("AI categorization"));
    expect(ai.getByText(/never account names or numbers/)).toBeInTheDocument();
    expect(ai.getByRole("link", { name: "OpenRouter key" })).toHaveAttribute("href", "https://openrouter.ai/keys");
    const model = ai.getByLabelText("Categorization model");
    expect(model).toHaveValue("");
    expect(model).toHaveAttribute("placeholder", "vendor/default-a");
    const cardModel = ai.getByLabelText("Card lookup model");
    expect(cardModel).toHaveValue("vendor/mine");
    expect(cardModel).toHaveAttribute("placeholder", "vendor/default-b");
    await userEvent.clear(cardModel);
    await userEvent.tab();
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/settings", { method: "POST", body: { card_ai_model: null } }));
  });

  it("puts an AI switch back when saving it fails", async () => {
    serve(status());
    vi.mocked(api).mockImplementation(async (path: string) => {
      if (path === "/api/settings") throw new Error("Couldn’t save");
      if (path === "/api/plaid/status") return status();
      if (path === "/api/retail") throw new Error("no");
      return {};
    });
    state.auto_ai_on_sync = true;
    render(ConnectionsSection);
    const box = within(card("AI categorization")).getByLabelText(/Categorize new merchants during each sync/);
    expect(box).toBeChecked();
    await userEvent.click(box);
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Couldn’t save"));
    expect(box).toBeChecked();
  });

  it("searches the web for card suggestions unless you switch it off", async () => {
    serve(status());
    render(ConnectionsSection);
    await screen.findByRole("button", { name: "Connect a bank or card" });
    const ai = within(card("AI categorization"));
    const web = ai.getByLabelText(/Search the web when filling in a card/);
    expect(web).toBeChecked();
    expect(ai.queryByText(/OpenRouter charges for the search/)).toBeNull();
    await userEvent.click(web);
    await waitFor(() => expect(vi.mocked(api)).toHaveBeenCalledWith("/api/settings", { method: "POST", body: { churn_ai_web: false } }));
  });
});
