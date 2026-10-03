// After you pick a category: offer to use it for this merchant from now on (a rule), as the main button of the change's
// Undo toast ("Always for Whole Foods"), instead of a checkbox you have to remember to tick. After you link a
// transaction to a recurring item none of whose texts is on it: offer to match its text from now on, in its toast.
// What you did is already saved; letting the toast go leaves it at that.
import { api } from "$lib/api";
import { refreshState } from "$lib/app.svelte";
import { UNDO_MS } from "$lib/undo";
import type { Also } from "$lib/undoBatch";
import { toast } from "svelte-sonner";
import type { RuleOffer } from "./types";

/**
 * The rule offer as an Undo toast's extra action ("Always for Blue Bottle"), and the line saying what it would do
 * ("matches “blue bottle” · +3 more": the others it would categorize too), from what the API tells. The toast's own
 * text is then just the category ("Groceries · Always for Blue Bottle · Undo").
 */
export function ruleOffer(txId: string, category: string, offer: RuleOffer, reload: () => void): { also: Also; description: string } {
  const what = [offer.match ? `matches “${offer.match}”` : "", offer.replaces ? `instead of ${offer.replaces}` : "",
    offer.also_updated ? `+${offer.also_updated} more` : ""].filter(Boolean).join(" · ");
  // A long name would squeeze the toast's text: the button is just "Always" then, and the name in the line under it.
  const short = offer.merchant.length <= 16;
  return { also: { label: short ? `Always for ${offer.merchant}` : "Always", run: () => always(txId, category, offer, reload) },
    description: short ? what : [`for ${offer.merchant}`, what].filter(Boolean).join(" · ") };
}

/** "Always": save the category again, this time as a rule for the merchant. Throws what went wrong (the toast says it). */
export async function always(txId: string, category: string, offer: RuleOffer, reload: () => void): Promise<void> {
  const r = await api<{ also_updated: number }>(`/api/transactions/${encodeURIComponent(txId)}/category`,
    { method: "POST", body: { category, remember: true } });
  toast.success(r.also_updated ? `From now on, ${offer.merchant} is ${category} · ${r.also_updated} more updated`
    : `From now on, ${offer.merchant} is ${category}`);
  refreshState();
  if (r.also_updated) reload();
}

/** "Linked to Paycheck · Also match “online transfer from savings”", for the recurring item a transaction was just linked to. */
export function askAlsoMatch(recurringId: number, name: string, text: string, reload: () => void): void {
  toast(`Linked to ${name}`, {
    description: `Also match “${text}” from now on?`,
    duration: UNDO_MS,
    action: { label: "Also match", onClick: () => alsoMatch(recurringId, name, text, reload) },
  });
}

/** "Also match": add the text to the item, so the next transaction with it links by itself. */
export async function alsoMatch(recurringId: number, name: string, text: string, reload: () => void): Promise<void> {
  try {
    const r = await api<{ linked: number }>(`/api/recurring/${recurringId}/match`, { method: "POST", body: { text } });
    toast.success(r.linked ? `${name} also matches “${text}” now · ${r.linked} more linked` : `${name} also matches “${text}” now`);
    if (r.linked) reload();
  } catch (err) { toast.error((err as Error).message); }
}
