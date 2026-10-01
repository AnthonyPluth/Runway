// The shapes of the API replies Settings uses (see runway/server.py, rules.py, plaid.py, retail.py, push.py).
import type { Account } from "$lib/types";

/** A row of GET /api/accounts, with what Settings → Accounts edits. */
export interface SettingsAccount extends Account {
  org?: string | null;
  pay_from?: string | null;
  owed_positive?: number;
  daily_spend?: number;
  networth_hidden?: number;
  provider?: string | null;
  plaid_account_id?: string | null;
  plaid_link?: { transactions?: boolean | number; institution?: string | null; mask?: string | null; closed?: string | boolean | number | null;
    statement_note?: string | null } | null;
  /** Cards: the statement the forecast uses (Plaid's, else the latest you entered), or null for none. */
  statement?: CardStatementInfo | null;
  /** Cards: the statements you entered, newest first. */
  statements?: ManualStatement[];
  /** Loans: the terms the retirement planner projects what's owed with (runway/loans.py terms()). */
  loan?: LoanTerms;
}

/** A loan's terms: `rate` (annual %) and `payment` are what's used; `plaid` when the rate is the lender's, through
 *  Plaid, and `plaid_payment` when the payment is (each then can't be set here); `set_rate`/`set_payment` are what you
 *  set; `inferred_payment` comes from the payments into the account lately. */
export interface LoanTerms {
  rate: number | null; payment: number | null; maturity?: string | null; source: "plaid" | "manual" | "inferred" | null;
  plaid: boolean; plaid_payment: boolean; set_rate: number | null; set_payment: number | null; inferred_payment: number | null;
}

export interface CardStatementInfo {
  source: "plaid" | "manual"; closed: string; due?: string | null; balance?: number | null; minimum?: number | null;
  /** Plaid's: the bank it's from. */
  institution?: string | null;
  /** One you entered: whether a newer one should have been entered by now, and when it's expected to close. */
  stale?: boolean; next_close?: string;
}
export interface ManualStatement { statement_date: string; balance: number; due_date: string; minimum_payment?: number | null }
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
