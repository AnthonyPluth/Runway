// What the report endpoints send back (runway/reports.py and api_cashflow in runway/server.py).

/** /api/reports/spending: each month's spending by category, merchant or account. */
export interface SpendingReport {
  months: string[];
  group: "category" | "merchant" | "account";
  series: { name: string; values: number[]; total: number; other?: boolean; members?: string[] }[];
  totals: number[];
}

/** /api/reports/income */
export interface IncomeReport {
  months: { month: string; income: number; spending: number; net: number; rate: number | null }[];
  year: { year: number; months: number; income: number; spending: number; net: number; rate: number | null };
}

/** /api/reports/merchants */
export interface MerchantsReport {
  start: string; end: string; count: number; total: number;
  merchants: { name: string; total: number; count: number; average: number; last: string; category: string }[];
}

/** /api/reports/merchant: one merchant's months and transactions. */
export interface MerchantReport {
  name: string; months: string[]; values: number[]; total: number;
  transactions: { id: string; posted: string; amount: number; category: string | null; account_name: string; description: string }[];
}

/** /api/reports/breakdown: top-level category -> subcategory -> merchant. */
export interface BreakdownNode { name: string; value: number; children?: BreakdownNode[] }
export interface BreakdownReport { start: string; end: string; tree: BreakdownNode & { children: BreakdownNode[] } }

/** /api/reports/transactions */
export interface ReportTx { id: string; posted: string; amount: number; payee: string; category: string | null; account_name: string; part: boolean }

/** /api/cashflow: the month for the Sankey. */
export interface CashflowNode { name: string; value: number }
export interface Cashflow {
  month: string;
  income: CashflowNode[];
  spending: (CashflowNode & { children: CashflowNode[] })[];
  total_in: number; total_out: number; net: number;
}

/** A series for the bar charts. */
export interface BarSeries { name: string; values: number[]; color: string }
