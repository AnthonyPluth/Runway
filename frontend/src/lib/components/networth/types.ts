// The shapes of the Net worth page's API replies (runway/server.py, runway/domain/networth.py, runway/domain/equity.py).

interface NwItem {
  type: "account" | "asset" | "equity";
  id: string | number;
  name: string;
  value: number;
  as_of?: string | null;
  org?: string | null;
  source?: string | null;
  kind?: string;
  loan?: { account_id: string; name: string; owed: number };
  equity?: number;
  /** A loan paid down since its last balance on its terms (runway/domain/loans.py owed_on): what that balance was. */
  synced?: number;
}
export interface NwGroup { key: string; label: string; side: "asset" | "liability"; items: NwItem[]; total: number }

export const ASSET_KIND_LABEL: Record<string, string> = { home: "Home / property", vehicle: "Vehicle", other: "Other" };

export interface Asset {
  id: number;
  name: string;
  kind: "home" | "vehicle" | "other" | string;
  value: number | null;
  as_of: string | null;
  source: string | null;
  yearly_change: number | null;
  address: string | null;
  url: string | null;
  loan_account_id: string | null;
  auto_update: number;
  low: number | null;
  high: number | null;
  last_lookup: string | null;
  /** When the home can next be looked up (once a week at most), or null if it can be now. */
  next_lookup: string | null;
  current_value: number;
  /** A home Realie values (set up, an address it can use, and its value from Realie): its value isn't typed by hand. */
  realie_valued?: boolean;
}

/** GET /api/networth */
export interface NetWorth {
  today: string;
  net: number;
  assets: number;
  liabilities: number;
  groups: NwGroup[];
  /** Accounts you left out of net worth (still shown elsewhere), to bring back. */
  excluded: { id: string; name: string; org: string | null; kind: string; /** Signed like the groups: what you owe, positive, for credit and loans. */ balance: number }[];
  history: { date: string; net: number; assets: number; liabilities: number }[];
  first_snapshot: string | null;
  change: Record<"30d" | "90d" | "1y", number | null>;
  /** The snapshot each change is measured from: snapshots are only saved on days the page is opened, so it can be older. */
  change_since?: Record<"30d" | "90d" | "1y", string | null>;
  assets_list: Asset[];
  loan_accounts: { id: string; name: string; kind: string }[];
  /** The home value service: set up or not, and its lookups this month. */
  realie: { configured: boolean; used: number; limit: number };
}

type GrantKind = "iso" | "nso" | "rsu" | "rsa" | "shares";
export interface Grant {
  id: string;
  company_id: string;
  kind: GrantKind;
  label: string | null;
  granted_on: string | null;
  quantity: number;
  strike: number | null;
  vest_start: string | null;
  vest_months: number | null;
  cliff_months: number | null;
  vest_every: number | null;
  exercised: number | null;
  expires_on: string | null;
  vested: number;
  fully_vested_on: string | null;
  schedule: [string, number][];
  problem?: string | null;   // why the schedule couldn't be worked out (a length or date past the limits), if it couldn't
  vested_value: number;
  unvested_value: number;
}
export interface Company {
  id: string;
  name: string;
  share_price: number | null;
  price_as_of: string | null;
  in_networth: number;
  source: "manual" | "carta" | string;
  grants: Grant[];
  vested_value: number;
  unvested_value: number;
}
interface CartaSettings {
  env: "production" | "playground" | "mock" | string;
  client_id: string | null;
  has_secret: boolean;
  connected: boolean;
  last_sync: string | null;
  last_error: string | null;
  web_last: string | null;
  web_error: string | null;
  web_capture: boolean;
}
/** GET /api/equity */
export interface Equity {
  companies: Company[];
  vested_value: number;
  unvested_value: number;
  in_networth: number;
  carta: CartaSettings;
}

/** What a grant form sends: every field as typed. */
export type GrantBody = Record<"kind" | "label" | "quantity" | "strike" | "exercised" | "granted_on" | "vest_start" | "vest_months" | "cliff_months" | "vest_every" | "expires_on", string>;
