// After you pick a category: offer to use it for this merchant from now on (a rule), instead of a checkbox you have
// to remember to tick. After you link a transaction to a recurring item none of whose texts is on it: offer to match
// its text from now on. What you did is already saved; ignoring the question leaves it at that. Kept outside the page
// so the question stays up while the list below it loads again. One question at a time.
import { api } from "$lib/api";
import { refreshState } from "$lib/app.svelte";
import { toast } from "svelte-sonner";
import type { RuleOffer } from "./types";

export const remember = $state({
  ask: null as { txId: string; category: string; offer: RuleOffer; reload: () => void } | null,
  match: null as { recurringId: number; name: string; text: string; reload: () => void } | null,
});
let timer: ReturnType<typeof setTimeout>;

function open(): void {
  toast.dismiss();
  closeRemember();
  timer = setTimeout(closeRemember, 12000);
}

export function askRemember(txId: string, category: string, offer: RuleOffer, reload: () => void): void {
  open();
  remember.ask = { txId, category, offer, reload };
}

/** "Also match “online transfer from savings” from now on?", for the recurring item a transaction was just linked to. */
export function askAlsoMatch(recurringId: number, name: string, text: string, reload: () => void): void {
  open();
  remember.match = { recurringId, name, text, reload };
}

export function closeRemember(): void {
  clearTimeout(timer);
  remember.ask = null;
  remember.match = null;
}

/** "Also match": add the text to the item, so the next transaction with it links by itself. */
export async function alsoMatch(): Promise<void> {
  const m = remember.match;
  if (!m) return;
  closeRemember();
  try {
    const r = await api<{ linked: number }>(`/api/recurring/${m.recurringId}/match`, { method: "POST", body: { text: m.text } });
    toast.success(r.linked ? `${m.name} also matches “${m.text}” now · ${r.linked} more linked` : `${m.name} also matches “${m.text}” now`);
    if (r.linked) m.reload();
  } catch (err) { toast.error((err as Error).message); }
}

/** "Always": save the category again, this time as a rule for the merchant. */
export async function always(): Promise<void> {
  const a = remember.ask;
  if (!a) return;
  closeRemember();
  try {
    const r = await api<{ also_updated: number }>(`/api/transactions/${encodeURIComponent(a.txId)}/category`,
      { method: "POST", body: { category: a.category, remember: true } });
    toast.success(r.also_updated ? `From now on, ${a.offer.merchant} is ${a.category} · ${r.also_updated} more updated`
      : `From now on, ${a.offer.merchant} is ${a.category}`);
    refreshState();
    if (r.also_updated) a.reload();
  } catch (err) { toast.error((err as Error).message); }
}
