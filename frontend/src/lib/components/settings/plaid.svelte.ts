// Plaid Link: loaded from Plaid's servers only when you connect an account. The page's Content-Security-Policy
// (runway/server.py) lets it in: the app's own <script> carries the page's nonce, and 'strict-dynamic' trusts the
// script it adds here; Link's iframe comes from cdn.plaid.com, which frame-src allows.
import { api } from "$lib/api";
import { reload } from "$lib/app.svelte";
import { toast } from "svelte-sonner";

interface PlaidLinkMeta { link_session_id?: string; request_id?: string; institution?: { name?: string } | null }
interface PlaidLinkError { display_message?: string | null; error_message?: string | null }
interface PlaidHandler { open(): void }
declare global {
  interface Window {
    Plaid?: { create(opts: {
      token: string; receivedRedirectUri?: string;
      onSuccess: (publicToken: string, metadata: { institution?: unknown }) => void;
      onExit: (err: PlaidLinkError | null, metadata: PlaidLinkMeta) => void;
    }): PlaidHandler };
  }
}

/** The last Link session that ended without connecting, for quoting to Plaid support. */
export const plaidSession = $state({ last: null as { sid: string; request: string; at: string } | null });

export function loadPlaid(): Promise<void> {
  if (window.Plaid) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const sc = document.createElement("script");
    sc.src = "https://cdn.plaid.com/link/v2/stable/link-initialize.js";
    sc.onload = () => resolve();
    sc.onerror = () => reject(new Error("Couldn't load Plaid Link. Check your internet connection."));
    document.head.appendChild(sc);
  });
}

/** Connect (itemId null) or reconnect a connection. Resolves true once something was connected. */
export async function openPlaidLink(itemId: string | null, kind = "investments"): Promise<boolean> {
  await loadPlaid();
  const lt = await api<{ link_token: string; kind?: string }>("/api/plaid/link_token", { method: "POST", body: { item_id: itemId || null, kind } });
  if (kind === "bank" && lt.kind === "cards") toast("Your Plaid account doesn't have Transactions, so this connects card statements only.");
  return runPlaidLink(lt.link_token, itemId, lt.kind || kind);
}

type Linked = { bank?: boolean; accounts: number; matched?: string[]; statements?: number; holdings?: number; transactions?: number; hidden_simplefin?: string[] };
const s = (n: number | undefined, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** Runs Plaid Link. receivedRedirectUri: continuing after a bank's own sign-in page sent you back (OAuth). */
export function runPlaidLink(token: string, itemId: string | null, kind: string, receivedRedirectUri?: string): Promise<boolean> {
  return new Promise((resolve) => {
    window.Plaid!.create({
      token,
      ...(receivedRedirectUri ? { receivedRedirectUri } : {}),
      onSuccess: async (publicToken, metadata) => {
        try {
          toast(kind === "investments" ? "Connected. Pulling holdings and activity…" : "Connected. Reading accounts and statements…");
          const r = itemId
            ? await api<Linked>(`/api/plaid/items/${encodeURIComponent(itemId)}/sync`, { method: "POST" })
            : await api<Linked>("/api/plaid/exchange", { method: "POST", body: { public_token: publicToken, institution: metadata.institution, kind } });
          toast.success(r.bank
            ? `Found ${s(r.accounts, "account")}` + (r.matched?.length ? ` · matched ${r.matched.join(", ")}` : "") +
              (r.statements ? ` · ${s(r.statements, "card statement")}` : "")
            : `Synced ${s(r.accounts, "account")}, ${r.holdings} holdings, ${r.transactions} activities` +
              (r.hidden_simplefin?.length ? ` · hid the SimpleFIN copy of ${r.hidden_simplefin.join(", ")}` : ""));
        } catch (err) { toast.error((err as Error).message); }
        resolve(true);
      },
      onExit: (err, metadata) => {
        // Shown so you can quote it to Plaid support ("Link Session ID"); also in the browser console.
        const sid = metadata?.link_session_id;
        if (sid) console.info("Plaid Link session", sid, "request", metadata.request_id || "", "institution", metadata.institution?.name || "");
        if (err) toast.error(err.display_message || err.error_message || "Plaid closed");
        if (sid) plaidSession.last = { sid, request: metadata.request_id || "", at: new Date().toLocaleString() };
        resolve(false);
      },
    }).open();
  });
}

/** Back from a bank's sign-in page (/plaid/oauth?oauth_state_id=…, see plaid.redirect_uri): finish linking where it
 *  left off. main.ts calls this when the app opens at that address. */
export async function resumePlaidOAuth(): Promise<boolean> {
  const back = location.href;
  history.replaceState(null, "", "/#setup/connections");
  window.dispatchEvent(new HashChangeEvent("hashchange"));
  try {
    const p = await api<{ link_token: string; item_id: string | null; kind: string }>("/api/plaid/oauth_resume", { keep: true });
    await loadPlaid();
    const linked = await runPlaidLink(p.link_token, p.item_id, p.kind, back);
    if (linked) reload();
    return linked;
  } catch (err) { toast.error((err as Error).message); return false; }
}
