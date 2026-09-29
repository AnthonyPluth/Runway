// The shapes of the Churning page's API replies (runway/server/api/churning.py, runway/churning.py,
// runway/bank_bonuses.py).

export interface Eligibility {
  status: "now" | "later" | "never" | "in_progress" | "held" | "au" | "unknown";
  on: string | null;
  why: string;
  override: boolean;
  rule?: string;
  issuer?: string;
}

export interface ChurnCard {
  id: number;
  owner: string;
  issuer: string;
  product: string;
  family: string | null;
  account_id: string | null;
  opened_on: string;
  closed_on: string | null;
  status: "open" | "closed" | "product_changed";
  changed_from: number | null;
  authorized_user: number;
  business: number;
  annual_fee: number;
  fee_month: number | null;
  currency: string;
  base_rate: number;
  earn_note: string | null;
  bonus: number | null;
  bonus_spend: number | null;
  bonus_months: number;
  bonus_deadline: string | null;
  bonus_earned_on: string | null;
  manual_spend: number | null;
  eligible_on: string | null;
  notes: string | null;
  rates: { category: string; multiplier: number }[];
  currency_name: string;
  cents: number;
  bonus_value: number | null;
  fee_due: string | null;
  deadline: string | null;
  spent: number | null;
  spend_source: "account" | "manual";
  bonus_state: "active" | "met" | "earned" | "missed" | null;
  counts_524: boolean;
  falls_off: string;
  eligibility: Eligibility;
  points_ytd: number | null;
  value_ytd: number | null;
}

export interface BankProgress {
  dd_total: number;
  dd_count: number | null;
  debits: number;
  balance: number | null;
  balance_ok: boolean | null;
  met: boolean;
  source: "account" | "manual";
}

export interface BankBonus {
  id: number;
  owner: string;
  bank: string;
  account_type: "checking" | "savings" | "business";
  account_id: string | null;
  opened_on: string;
  bonus: number;
  dd_total: number | null;
  dd_count: number | null;
  debit_count: number | null;
  min_balance: number | null;
  hold_until: string | null;
  other_reqs: string | null;
  deadline_days: number;
  deadline: string | null;
  post_days: number;
  manual_dd: number | null;
  manual_debits: number | null;
  status: "open" | "pending" | "received" | "closed";
  received_on: string | null;
  received_amount: number | null;
  closed_on: string | null;
  monthly_fee: number;
  fee_waiver: string | null;
  early_close_fee: number | null;
  keep_open_days: number | null;
  repeat_months: number | null;
  once_per_lifetime: number;
  eligible_on: string | null;
  notes: string | null;
  progress: BankProgress;
  state: "active" | "met" | "received" | "closed" | "missed";
  due: string;
  expected_on: string | null;
  safe_close_on: string | null;
  fee_reminder: string | null;
  eligibility: Eligibility;
}

export interface Five24 {
  owner: string;
  count: number;
  under: boolean;
  under_on: string | null;
  next_fall_off: string | null;
  cards: { id: number; product: string; issuer: string; opened_on: string; falls_off: string }[];
  timeline: { date: string; count: number }[];
}

export type UpcomingKind = "task" | "fee" | "bonus" | "five24" | "eligible"
  | "bank_due" | "bank_hold" | "bank_post" | "bank_close" | "bank_fee" | "bank_eligible";

export interface UpcomingItem {
  date: string;
  kind: UpcomingKind;
  card_id: number | null;
  bank_id?: number;
  task_id?: number;
  owner: string | null;
  title: string;
  detail: string;
  warn: boolean;
}

export interface Currency { key: string; name: string; cents: number; default: number | null; custom: boolean }

export interface RewardRow {
  currency: string; name: string; earned: number; bonuses: number; balance: number | null; as_of?: string | null;
  cents: number; value: number; balance_value: number | null;
}

export interface Task { id: number; card_id: number; due_on: string; action: string; done: number; product: string | null; owner: string | null }

export interface Churning {
  today: string;
  people: string[];
  cards: ChurnCard[];
  bank: BankBonus[];
  bank_income: Record<string, Record<string, number>>;
  five24: Record<string, Five24>;
  upcoming: UpcomingItem[];
  rewards: Record<string, { currencies: RewardRow[]; value: number; balance_value: number }>;
  tasks: Task[];
  currencies: Currency[];
  issuers: { key: string; name: string; rule: string }[];
  accounts: { id: string; name: string; owner: string | null }[];
  bank_accounts: { id: string; name: string; owner: string | null }[];
}

export interface BestCard {
  id: number; owner: string; product: string; issuer: string; multiplier: number; currency: string; cents: number;
  return_pct: number; value: number | null;
  bonus: { remaining: number; deadline: string; amount: number | null; currency: string } | null;
}
