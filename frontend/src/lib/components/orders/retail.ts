// Amazon and Target orders (runway/retail.py), shared by Transactions (an order under its charge) and Settings.

export interface RetailItem {
  id: number; title: string; quantity: number; amount: number; department?: string | null;
  category?: string | null; category_source?: string | null; confidence?: number | null;
}
export interface RetailCharge {
  id: string; date: string; amount: number; payment?: string | null; tx_id?: string | null; match_source?: string | null;
  applied?: "split" | "category" | null; posted?: string | null; payee?: string | null; description?: string | null; account_name?: string | null;
}
/** GET /api/retail/orders/{id} */
export interface RetailOrder {
  id: string; retailer: "amazon" | "target" | string; order_number: string; channel?: string | null; placed?: string | null;
  total?: number | null; subtotal?: number | null; tax?: number | null; shipping?: number | null; payment?: string | null;
  details?: number | boolean; items: RetailItem[]; charges: RetailCharge[]; url: string;
}
/** A transaction's order, as /api/transactions sends it (and a line of /api/retail's recent orders). */
export interface OrderSummary { order_id?: string; id?: string; retailer: string; channel?: string | null; items?: number }

export const STORES: Record<string, string> = { amazon: "Amazon", target: "Target", costco: "Costco" };
export const STORE_SITES: Record<string, string> = { amazon: "amazon.com", target: "target.com", costco: "costco.com" };
export const ITEM_SOURCES: Record<string, string> = { manual: "you picked", memory: "as before", ai: "AI", department: "store's department" };

/** "Amazon · 3 items", "Target in store · 1 item" */
export const orderLabel = (o: OrderSummary) =>
  `${STORES[o.retailer] || o.retailer}${o.channel === "store" ? " in store" : ""}${o.items ? ` · ${o.items} item${o.items === 1 ? "" : "s"}` : ""}`;
