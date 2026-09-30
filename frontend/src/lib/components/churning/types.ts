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

// A card's earning rate in a category. `category` "*" (Churning.base_marker) in a request stands for the card's base
// rate. portal_only: earned only when booked through the issuer's portal (ChurnCard.portal_name); the API always
// sends it, it's optional so a rate built in a form before saving type-checks.
export interface ChurnRate { category: string; multiplier: number; portal_only?: boolean }

export type Plan = "undecided" | "keep" | "close" | "product_change";

export type BenefitKind = "credit" | "access" | "status" | "other";
export type BenefitPeriod = "monthly" | "quarterly" | "semiannual" | "annual" | "every_4_years" | "one_time";
export type BenefitBasis = "calendar" | "anniversary";

// A card benefit (runway/churn_benefits.py), as stored plus this period's figures.
export interface Benefit {
  id: number;
  card_id: number;
  name: string;
  kind: BenefitKind;
  amount: number | null;           // a credit: dollars per period
  period: BenefitPeriod;
  basis: BenefitBasis;
  annual_value: number | null;     // your own figure a year (access: its only value)
  counts: number;                  // 1: counts against the annual fee (you'll use it)
  remind: number;
  remind_days: number | null;      // null: 14 for monthly/quarterly, else 30 (see lead_days)
  expires_on: string | null;       // one_time only
  preset: string | null;
  notes: string | null;
  active: number;
  period_start: string;
  period_end: string | null;       // null: a one-time benefit without an expiry
  used: number | null;             // dollars used this period (credits); null otherwise
  used_count: number;              // uses this period
  remaining: number | null;        // dollars left this period (credits)
  value_per_year: number;
  lead_days: number;
  days_left: number | null;
  expiring: boolean;               // money left and the period ends within lead_days
  remind_now: boolean;             // ... and reminders are on: it's in Upcoming
  uses: { id: number; amount_used: number | null; used_on: string }[];
}

export interface BenefitPreset { group: string; key: string; name: string; kind: BenefitKind; period: BenefitPeriod; basis: BenefitBasis }

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
  portal_name: string | null;
  plan: Plan;
  plan_target: string | null;
  plan_date: string | null;        // as set; see plan_due for the day in effect
  plan_remind_days: number;
  plan_done_on: string | null;
  plan_new_id: number | null;      // the card checking the plan off added (undo removes it)
  hide_upcoming: number;
  plan_due: string | null;         // plan_date, else the day before the next annual fee
  plan_active: boolean;            // close/product_change, not done, card open
  benefits: Benefit[];
  benefits_value: number;          // a year, the benefits that count
  net_fee: number;                 // annual_fee − benefits_value (can be negative)
  points_note: string | null;      // points_ytd leaves out portal-only rates
  rates: ChurnRate[];
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

export type UpcomingKind = "task" | "fee" | "plan" | "bonus" | "benefit" | "five24" | "eligible" | "apply" | "offer_ends"
  | "bank_due" | "bank_hold" | "bank_post" | "bank_close" | "bank_fee" | "bank_eligible";

export interface UpcomingItem {
  date: string;
  kind: UpcomingKind;
  card_id: number | null;
  bank_id?: number;
  task_id?: number;
  benefit_id?: number;             // kind "benefit"
  remaining?: number;              // kind "benefit": dollars left
  wish_id?: number;                // kinds "apply" and "offer_ends"
  plan?: Plan;                     // kinds "fee" and "plan"
  owner: string | null;
  title: string;
  detail: string;
  warn: boolean;
}

export type CurrencyKind = "bank" | "airline" | "hotel" | "cash" | "other";

// cents: in effect. default: the built-in estimate (null for your own currencies). overridden: you set your own
// value for a built-in one. estimate: still the built-in estimate (as of `as_of`).
export interface Currency {
  key: string; name: string; kind: CurrencyKind; cents: number; default: number | null; custom: boolean;
  overridden: boolean; estimate: boolean; as_of: string | null; source_note: string;
}

export interface CurrencyGroup { kind: CurrencyKind; label: string; keys: string[] }

export interface RewardRow {
  currency: string; name: string; earned: number; bonuses: number; balance: number | null; as_of?: string | null;
  cents: number; value: number; balance_value: number | null;
  // An estimate of the balance now: what you entered plus the points the cards earned since its day (null when
  // they earned none since).
  earned_since?: number; est_balance?: number | null; est_value?: number | null;
}

export interface Task {
  id: number; card_id: number; due_on: string; action: string; done: number; snooze_until: string | null;
  product: string | null; owner: string | null;
}

export interface Blocker { kind: "five24" | "bonus_rule" | "held" | "wait" | "score" | "offer"; text: string; date: string | null }

// A planned card or bank bonus (runway/churn_wishlist.py) and what's in the way of applying.
export interface Wish {
  id: number;
  owner: string;
  kind: "card" | "bank_bonus";
  issuer: string | null;           // card
  bank: string | null;             // bank bonus
  product: string | null;
  family: string | null;
  business: number;
  annual_fee: number | null;
  bonus: number | null;
  currency: string | null;
  bonus_spend: number | null;
  bonus_months: number | null;
  account_type: "checking" | "savings" | "business" | null;
  requirements: string | null;
  repeat_months: number | null;
  once_per_lifetime: number;
  offer_expires_on: string | null;
  priority: number | null;         // 1 = next, per person
  status: "wanted" | "ready" | "applied" | "dropped";
  wait_until: string | null;
  min_score: number | null;
  assume_prior_planned: number;
  notes: string | null;
  applied_on: string | null;
  applied_id: number | null;       // churn card or bank bonus id, by kind
  blockers: Blocker[];
  hints: string[];                 // soft notes, not blockers ("still spending toward X's bonus")
  earliest_apply: string | null;   // the latest dated blocker; null when none is dated
  ready: boolean;                  // no blockers (and still wanted)
}

export interface CreditScore {
  owner: string; score: number; as_of: string; source: string | null;
  history: { score: number; as_of: string; source: string | null }[];
}

export interface AlertPref { key: "churn_fee" | "churn_bonus" | "churn_plan" | "churn_benefit" | "churn_apply"; label: string; on: boolean }

export interface Churning {
  today: string;
  people: string[];
  owners: string[];                // the choices for whose card or bonus it is (same as people)
  cards: ChurnCard[];
  bank: BankBonus[];
  bank_income: Record<string, Record<string, number>>;
  five24: Record<string, Five24>;
  upcoming: UpcomingItem[];
  rewards: Record<string, { currencies: RewardRow[]; value: number; balance_value: number }>;
  tasks: Task[];
  currencies: Currency[];
  currency_groups: CurrencyGroup[];
  values_as_of: string;
  values_note: string;
  issuers: { key: string; name: string; rule: string }[];
  plans: Plan[];
  categories: { name: string; parent: string | null }[];   // spending categories, for earning rates
  base_marker: string;
  benefit_presets: BenefitPreset[];
  benefit_kinds: { key: BenefitKind; name: string }[];
  benefit_periods: { key: BenefitPeriod; name: string }[];
  benefit_bases: { key: BenefitBasis; name: string }[];
  wishlist: Wish[];
  scores: Record<string, CreditScore>;
  alert_prefs: AlertPref[];
  accounts: { id: string; name: string; owner: string | null }[];
  bank_accounts: { id: string; name: string; owner: string | null }[];
}

export interface PortalOption { multiplier: number; return_pct: number; portal_name: string | null; value: number | null; note: string }

// GET /api/churning/best?category=&owner=&amount=&portal=1 → { category, portal, cards: BestCard[] }
export interface BestCard {
  id: number; owner: string; product: string; issuer: string; multiplier: number; currency: string; cents: number;
  return_pct: number; value: number | null;
  bonus: { remaining: number; deadline: string; amount: number | null; currency: string } | null;
  needs_portal: boolean;           // the rate counted is portal-only (only with portal=1)
  portal_name: string | null;
  portal_option: PortalOption | null;   // a better portal-only rate you could get (without portal=1)
  note: string | null;             // "10x if booked through Capital One Travel" / "Only when booked through ..."
}

// POST /api/churning/cards/{id}/plan/done and /plan/undo
export interface PlanResult {
  id: number; plan: Plan; plan_done_on: string | null; status: ChurnCard["status"]; closed_on: string | null;
  new_card_id: number | null; changes: string[];
}
