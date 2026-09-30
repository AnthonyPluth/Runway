// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("$lib/app.svelte", () => ({ reload: vi.fn(), refreshState: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

import { api } from "$lib/api";
import { refreshState, reload } from "$lib/app.svelte";
import { toast } from "svelte-sonner";
import { connectPlaid, openPlaidLink, plaidSession, resumePlaidOAuth, runPlaidLink } from "./plaid.svelte";

type Opts = Parameters<NonNullable<Window["Plaid"]>["create"]>[0];
let opts: Opts;
const open = vi.fn();
beforeEach(() => {
  vi.mocked(api).mockReset();
  vi.clearAllMocks();
  window.Plaid = { create: (o) => { opts = o; return { open }; } };
  plaidSession.last = null;
});
afterEach(() => { delete window.Plaid; });

describe("runPlaidLink", () => {
  it("opens Link with the token, and passes along the return address after a bank's own sign-in", () => {
    runPlaidLink("tok", null, "bank", "https://runway/plaid/oauth?x=1");
    expect(open).toHaveBeenCalled();
    expect(opts).toMatchObject({ token: "tok", receivedRedirectUri: "https://runway/plaid/oauth?x=1" });
  });

  it("exchanges the public token for a new bank connection and says what it found", async () => {
    vi.mocked(api).mockResolvedValue({ bank: true, accounts: 2, matched: ["Checking"], statements: 1 });
    const done = runPlaidLink("tok", null, "bank");
    await opts.onSuccess("public", { institution: { name: "Chase" } });
    expect(await done).toBe(true);
    expect(api).toHaveBeenCalledWith("/api/plaid/exchange", { method: "POST", body: { public_token: "public", institution: { name: "Chase" }, kind: "bank" } });
    expect(toast.success).toHaveBeenCalledWith("Found 2 accounts · matched Checking · 1 card statement");
  });

  it("says so, without an error, when the new connection's first sync waits for one already running", async () => {
    vi.mocked(api).mockResolvedValue({ ok: true, connected: true, sync_deferred: true, message: "Connected. A sync is running, so …" });
    const done = runPlaidLink("tok", null, "bank");
    await opts.onSuccess("public", { institution: { name: "Chase" } });
    expect(await done).toBe(true);
    expect(toast).toHaveBeenLastCalledWith("Connected. A sync is running, so …");
    expect(toast.error).not.toHaveBeenCalled();
    expect(toast.success).not.toHaveBeenCalled();
    expect(refreshState).toHaveBeenCalled();
  });

  it("re-syncs an existing connection instead of exchanging a token when reconnecting", async () => {
    vi.mocked(api).mockResolvedValue({ accounts: 1, holdings: 12, transactions: 30, hidden_simplefin: ["Brokerage"] });
    const done = runPlaidLink("tok", "item/1", "investments");
    await opts.onSuccess("public", {});
    await done;
    expect(api).toHaveBeenCalledWith("/api/plaid/items/item%2F1/sync", { method: "POST" });
    expect(toast.success).toHaveBeenCalledWith("Synced 1 account, 12 holdings, 30 activities · hid the SimpleFIN copy of Brokerage");
  });

  it("still resolves true, with the error shown, when the exchange fails", async () => {
    vi.mocked(api).mockRejectedValue(new Error("Exchange failed"));
    const done = runPlaidLink("tok", null, "investments");
    await opts.onSuccess("public", {});
    expect(await done).toBe(true);
    expect(toast.error).toHaveBeenCalledWith("Exchange failed");
  });

  it("resolves false when you close Link, keeping the session id to quote to support", async () => {
    vi.spyOn(console, "info").mockImplementation(() => {});
    const done = runPlaidLink("tok", null, "bank");
    opts.onExit({ display_message: "Bank is down" }, { link_session_id: "sess-1", request_id: "req-1" });
    expect(await done).toBe(false);
    expect(toast.error).toHaveBeenCalledWith("Bank is down");
    expect(plaidSession.last).toMatchObject({ sid: "sess-1", request: "req-1" });
  });

  it("closing without an error is quiet", async () => {
    const done = runPlaidLink("tok", null, "bank");
    opts.onExit(null, {});
    expect(await done).toBe(false);
    expect(toast.error).not.toHaveBeenCalled();
    expect(plaidSession.last).toBeNull();
  });
});

describe("openPlaidLink", () => {
  it("asks for a link token and opens Link", async () => {
    vi.mocked(api).mockResolvedValue({ link_token: "tok-1" });
    const p = openPlaidLink(null, "bank");
    await vi.waitFor(() => expect(open).toHaveBeenCalled());
    expect(api).toHaveBeenCalledWith("/api/plaid/link_token", { method: "POST", body: { item_id: null, kind: "bank" } });
    expect(opts.token).toBe("tok-1");
    opts.onExit(null, {});
    expect(await p).toBe(false);
  });

  it("tells you when your Plaid account only connects card statements", async () => {
    vi.mocked(api).mockResolvedValue({ link_token: "t", kind: "cards" });
    const p = openPlaidLink(null, "bank");
    await vi.waitFor(() => expect(open).toHaveBeenCalled());
    expect(toast).toHaveBeenCalledWith(expect.stringContaining("card statements only"));
    opts.onExit(null, {});
    await p;
  });

  it("loads Plaid's script when it isn't there yet, and fails clearly when it can't", async () => {
    delete window.Plaid;
    vi.mocked(api).mockResolvedValue({ link_token: "t" });
    const p = openPlaidLink(null);
    const script = document.head.querySelector<HTMLScriptElement>("script[src*=plaid]")!;
    expect(script).toBeTruthy();
    script.onerror!(new Event("error"));
    await expect(p).rejects.toThrow("Couldn't load Plaid Link");
    script.remove();
  });
});

describe("resumePlaidOAuth", () => {
  it("finishes linking after a bank's sign-in page and reloads the page", async () => {
    history.replaceState(null, "", "/plaid/oauth?oauth_state_id=abc");
    vi.mocked(api).mockResolvedValue({ link_token: "resume", item_id: null, kind: "bank" });
    const p = resumePlaidOAuth();
    await vi.waitFor(() => expect(open).toHaveBeenCalled());
    expect(opts).toMatchObject({ token: "resume", receivedRedirectUri: expect.stringContaining("oauth_state_id=abc") });
    expect(location.hash).toBe("#setup/connections");
    opts.onSuccess("public", {});
    await vi.waitFor(() => expect(reload).toHaveBeenCalled());
    expect(await p).toBe(true);
  });

  it("shows the error when it can't resume", async () => {
    vi.mocked(api).mockRejectedValue(new Error("Expired"));
    expect(await resumePlaidOAuth()).toBe(false);
    expect(toast.error).toHaveBeenCalledWith("Expired");
  });
});

describe("connectPlaid", () => {
  it("lands on Accounts after connecting a bank, where its accounts wait for a decision", async () => {
    location.hash = "#setup/connections";
    vi.mocked(api).mockResolvedValue({ link_token: "tok-1" });
    const p = connectPlaid("bank");
    await vi.waitFor(() => expect(open).toHaveBeenCalled());
    opts.onSuccess("public", {});
    await p;
    expect(location.hash).toBe("#setup/accounts");
    expect(reload).toHaveBeenCalled();
  });

  it("stays put after connecting an investment account, and when Link is closed", async () => {
    location.hash = "#setup/connections";
    vi.mocked(api).mockResolvedValue({ link_token: "tok-1" });
    const inv = connectPlaid("investments");
    await vi.waitFor(() => expect(open).toHaveBeenCalled());
    opts.onSuccess("public", {});
    await inv;
    expect(location.hash).toBe("#setup/connections");
    open.mockClear(); vi.mocked(reload).mockClear();
    const closed = connectPlaid("bank");
    await vi.waitFor(() => expect(open).toHaveBeenCalled());
    opts.onExit(null, {});
    await closed;
    expect(location.hash).toBe("#setup/connections");
    expect(reload).not.toHaveBeenCalled();
  });
});
