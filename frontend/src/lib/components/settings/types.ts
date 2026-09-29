// The shapes of the API replies Settings uses (see runway/server.py, rules.py, plaid.py, retail.py, push.py).
import type { Account } from "$lib/types";

/** A row of GET /api/accounts, with what Settings → Accounts edits. */
export interface SettingsAccount extends Account {
  org?: string | null;
  pay_from?: string | null;
  owed_positive?: number;
  daily_spend?: number;
  provider?: string | null;
  plaid_account_id?: string | null;
  plaid_link?: { transactions?: boolean | number; institution?: string | null; mask?: string | null; closed?: boolean | number;
    statement_note?: string | null } | null;
}

export interface SplitPart { category: string; percent: number | string }

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
/** GET /api/plaid/status */
export interface PlaidStatus {
  configured: boolean; env: string; client_id: string; items: PlaidItem[]; redirect_uri?: string | null;
}

export interface StoreStatus { name: string; last?: string | null; orders: number; read: number; matched: number; unmatched: number }
export interface RecentOrder {
  id: string; retailer: string; order_number: string; channel?: string | null; placed?: string | null; total?: number | null;
  details?: number; items?: number; charges?: number; matched?: number;
}
/** GET /api/retail */
export interface RetailStatus {
  token: boolean; token_created?: string | null; ai: boolean; stores: Record<"amazon" | "target", StoreStatus>; recent: RecentOrder[];
}

/** GET /api/equity (only the Carta part) */
export interface CartaStatus {
  env: string; client_id?: string | null; has_secret: boolean; connected: boolean; last_sync?: string | null; last_error?: string | null;
  web_last?: string | null; web_error?: string | null; web_capture?: boolean;
}

export type PushPrefs = Record<string, boolean | number>;
export interface PushDevice { endpoint: string; device?: string | null; created?: number | null; last_ok?: number | null; last_error?: string | null }
/** GET /api/push */
export interface PushInfo { public_key: string; prefs: PushPrefs; devices: PushDevice[]; recent: { title: string; sent: number }[] }
