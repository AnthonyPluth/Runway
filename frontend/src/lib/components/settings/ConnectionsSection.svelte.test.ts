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
import type { PlaidItem, PlaidStatus } from "./types";

const item = (over: Partial<PlaidItem> = {}): PlaidItem => ({
  item_id: "it1", institution_name: "Chase", products: ["transactions"], bank: true, last_sync: "2026-09-30 15:00:00",
  accounts: [{ id: "pa1", name: "Freedom", type: "credit", account_id: null }], ...over });
const status = (items: PlaidItem[] = [], over: Partial<PlaidStatus> = {}): PlaidStatus => ({ configured: true, env: "production", client_id: "cid", items, ...over });
const serve = (st: PlaidStatus | Error) => vi.mocked(api).mockImplementation(async (path: string) => {
  if (path === "/api/plaid/status") { if (st instanceof Error) throw st; return st; }
  return { ok: true, new: 3 };
});
const state = app.state as unknown as Record<string, unknown>;
// While a Sheet closes Bits UI leaves <body> non-interactive until the animation ends.
const settled = () => waitFor(() => expect(document.body.style.pointerEvents).not.toBe("none"));

beforeEach(() => {
  vi.mocked(api).mockReset(); vi.mocked(reload).mockClear(); vi.mocked(connectPlaid).mockClear();
  state.simplefin = false; state.last_log = null; state.sync_warnings = [];
});

describe("Settings → Bank connections", () => {
  it("makes Connect a bank the obvious next step when nothing is connected", async () => {
    serve(status());
    render(ConnectionsSection);
    expect(await screen.findByText("Nothing is connected yet")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Connect a bank" })).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Sync" })).toBeNull();
  });

  it("lists SimpleFIN and each Plaid connection as compact rows, with one Connect a bank button", async () => {
    state.simplefin = true; state.last_log = { at: "2026-09-30T14:00:00+00:00", ok: true, message: "12 new" };
    serve(status([item(), item({ item_id: "it2", institution_name: "Fidelity", bank: false, products: ["investments"], accounts: [] })]));
    render(ConnectionsSection);
    expect(await screen.findByText("Chase")).toBeInTheDocument();
    expect(screen.getByText("SimpleFIN")).toBeInTheDocument();
    expect(screen.getByText(/^Last sync Sep 30, 10:00\sAM · 12 new$/)).toBeInTheDocument();   // in this browser's time zone
    expect(screen.getByText("connected")).toBeInTheDocument();
    expect(screen.getByText("Fidelity")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Sync" })).toHaveLength(2);
    expect(screen.getAllByRole("button", { name: "Remove" })).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Replace the connection" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Connect a bank" })).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Connect an investment account" })).toBeNull();
  });

  it("shows SimpleFIN in amber, with what was said, when the last sync failed or a bank needs attention", async () => {
    state.simplefin = true; state.last_log = { at: "2026-09-30 14:00:00", ok: false, message: "SimpleFIN is down" };
    serve(status());
    const { unmount } = render(ConnectionsSection);
    expect(await screen.findByText("SimpleFIN is down")).toHaveClass("text-amber-500");
    expect(screen.getByText("needs attention")).toBeInTheDocument();
    expect(screen.getByText(/^Last sync Sep 30, 10:00\sAM$/)).toBeInTheDocument();   // the old UTC form reads as UTC
    unmount();
    state.last_log = { at: "2026-09-30T14:00:00+00:00", ok: true, message: "2 new transactions · bank messages: Chase: log in again" };
    state.sync_warnings = ["Chase: log in again"];
    render(ConnectionsSection);
    expect(await screen.findByText("Chase: log in again")).toHaveClass("text-amber-500");
    expect(screen.getByText(/^Last sync Sep 30, 10:00\sAM · 2 new transactions$/)).toBeInTheDocument();
  });

  it("offers Reconnect for a connection that needs it, and Replace opens the SimpleFIN token form", async () => {
    state.simplefin = true;
    serve(status([item({ error: "ITEM_LOGIN_REQUIRED" })]));
    render(ConnectionsSection);
    expect(await screen.findByRole("button", { name: "Reconnect" })).toBeInTheDocument();
    expect(screen.queryByLabelText("SimpleFIN setup token")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Replace the connection" }));
    expect(screen.getByLabelText("SimpleFIN setup token")).toBeInTheDocument();
  });

  it("connects through SimpleFIN with a setup token", async () => {
    serve(status());
    render(ConnectionsSection);
    await userEvent.click(await screen.findByRole("button", { name: "Connect a bank" }));
    const dialog = await screen.findByRole("dialog", { name: "Connect a bank" });
    await userEvent.click(within(dialog).getByRole("button", { name: /SimpleFIN/ }));
    await userEvent.type(within(dialog).getByLabelText("SimpleFIN setup token"), "tok123");
    await userEvent.click(within(dialog).getByRole("button", { name: "Connect and sync" }));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/connect", { method: "POST", body: { token: "tok123" } }));
    expect(api).toHaveBeenCalledWith("/api/sync", { method: "POST" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await settled();
    expect(reload).toHaveBeenCalled();
  });

  it("connects through Plaid as a bank or card, or as an investment account", async () => {
    serve(status());
    render(ConnectionsSection);
    await userEvent.click(await screen.findByRole("button", { name: "Connect a bank" }));
    let dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: /Plaid/ }));
    await userEvent.click(within(dialog).getByRole("button", { name: /Bank or card account/ }));
    await waitFor(() => expect(connectPlaid).toHaveBeenLastCalledWith("bank"));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await settled();

    await userEvent.click(screen.getByRole("button", { name: "Connect a bank" }));
    dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: /Plaid/ }));
    await userEvent.click(within(dialog).getByRole("button", { name: /Investment account/ }));
    await waitFor(() => expect(connectPlaid).toHaveBeenLastCalledWith("investments"));
  });

  it("explains that Plaid needs API keys, and opens them, when it isn't configured", async () => {
    serve(status([], { configured: false, client_id: "" }));
    render(ConnectionsSection);
    const keys = (await screen.findByText("Plaid API keys · not set")).closest("details")!;
    expect(keys.open).toBe(true);
    keys.open = false;
    await userEvent.click(screen.getByRole("button", { name: "Connect a bank" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: /Plaid/ }));
    expect(within(dialog).getByText(/Plaid needs API keys first/)).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: /Bank or card account/ })).toBeNull();
    await userEvent.click(within(dialog).getByRole("button", { name: "Add Plaid API keys" }));
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
