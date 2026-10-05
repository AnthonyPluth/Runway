// The shapes of the API replies Settings uses (see runway/server.py, rules.py, plaid.py, retail/, push.py).
import type { AccountItem, EnteredStatement, PlaidStatement } from "$lib/api-types";
import type { Account } from "$lib/types";

/** A row of GET /api/accounts (the contract's AccountItem), with what Settings → Accounts edits. Cards: how the forecast
 *  pays each statement (`pay_mode`, in full by default; `pay_amount`, the fixed amount; `apr` in percent, else the
 *  issuer's `issuer_apr` through Plaid), the statement the forecast uses (`statement`: Plaid's, else the latest you
 *  entered) and the ones you entered (`statements`, newest first). Loans: the terms the retirement planner projects
 *  what's owed with (`loan`, runway/loans.py terms()). */
export type SettingsAccount = Account & Partial<Omit<AccountItem, "id" | "name" | "kind" | "hidden">>;

/** A loan's terms: `rate` (annual %) and `payment` are what's used; `plaid` when the rate is the lender's, through
 *  Plaid, and `plaid_payment` when the payment is (each then can't be set here); `set_rate`/`set_payment` are what you
 *  set; `inferred_payment` comes from the payments into the account lately. */
export type { LoanTerms, ManualStatement } from "$lib/api-types";

/** A card's statement: Plaid's (with its bank), or one you entered (whether a newer one should have been entered by
 *  now, and when the next is expected to close). */
export type CardStatementInfo = PlaidStatement | EnteredStatement;
/** GET /api/accounts/{id}/removal: what deleting it takes with it. */
export interface AccountRemoval { name: string; transactions: number; recurring: number; rules: number; statements: number; holdings: number; plaid: boolean }
/** GET /api/accounts/deleted */
export interface DeletedAccount { id: string; name?: string | null; kind?: string | null; deleted_at?: string | null }

interface SplitPart { category: string; percent: number | string }

/** GET /api/rules */
export interface Rule {
  id?: number;
  match: string;
  match_mode?: "contains" | "exact" | "starts" | string;
  amount_min?: number | string | null;
  amount_max?: number | string | null;
  direction?: "in" | "out" | "" | null;
  account_id?: string | null;
  category?: string | null;
  rename?: string | null;
  review?: number | boolean;
  split?: SplitPart[] | null;
  summary?: string;
}

/** POST /api/rules/preview */
export interface RulePreview {
  error?: string;
  matches: number;
  changes: number;
  examples?: { posted: string; amount: number; payee?: string; description?: string; category?: string | null; account_name?: string }[];
}

export interface PlaidAccount {
  id: string; name?: string | null; official_name?: string | null; subtype?: string | null; type?: string | null;
  mask?: string | null; balance?: number | null; ignored?: number | boolean; account_id?: string | null;
}
export interface PlaidItem {
  item_id: string; institution_name?: string | null; env?: string; created_at?: string | null; last_sync?: string | null;
  error?: string | null; products: string[]; bank: boolean; duplicates?: { item_id: string; shared: number }[];
  accounts: PlaidAccount[];
  candidates?: { id: string; name: string; display_name?: string | null; balance?: number | null; linked_to?: string | null }[];
}
/** What SimpleFIN sends for an investment account (Settings' diagnostics). */
export interface SimplefinSeen { id?: string; org?: string; name: string; positions: number; fields: string[]; at?: string }
/** GET /api/plaid/status: Plaid's connections, and how investment syncing stands (Plaid's and SimpleFIN's). */
export interface PlaidStatus {
  configured: boolean; env: string; client_id: string; items: PlaidItem[]; redirect_uri?: string | null;
  last_inv_sync: string | null; syncing: boolean; inv_accounts: number;
  simplefin_connected: boolean; simplefin_last_sync: string | null; simplefin_seen: SimplefinSeen[];
}

interface StoreStatus { name: string; last?: string | null; orders: number; read: number; matched: number; unmatched: number }
export interface RecentOrder {
  id: string; retailer: string; order_number: string; channel?: string | null; placed?: string | null; total?: number | null;
  details?: number; items?: number; charges?: number; matched?: number;
}
/** GET /api/retail */
export interface RetailStatus {
  token: boolean; token_created?: string | null; token_used?: string | null; token_expires?: string | null;
  token_problem?: "expired" | "owner_gone" | null; ai: boolean; stores: Record<"amazon" | "target" | "costco", StoreStatus>; recent: RecentOrder[];
}

/** GET /api/equity (only the Carta part) */
export interface CartaStatus {
  env: string; client_id?: string | null; has_secret: boolean; connected: boolean; last_sync?: string | null; last_error?: string | null;
  web_last?: string | null; web_error?: string | null; web_capture?: boolean;
}

type PushPrefs = Record<string, boolean | number>;
export interface PushDevice {
  endpoint: string; device?: string | null; created?: number | null; last_ok?: number | null; last_error?: string | null;
  /** Turned on before there was sign-in: nobody's, so it gets nothing until it's turned on again from the device. */
  unclaimed?: boolean;
}
/** GET /api/push */
export interface PushInfo { public_key: string; prefs: PushPrefs; devices: PushDevice[]; recent: { title: string; sent: number }[] }
