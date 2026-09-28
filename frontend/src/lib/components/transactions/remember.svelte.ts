// After you pick a category: offer to use it for this merchant from now on (a rule), instead of a checkbox you have
// to remember to tick. The category is already saved; ignoring the question leaves it at that. Kept outside the page
// so the question stays up while the list below it loads again.
import { api } from "$lib/api";
import { refreshState } from "$lib/app.svelte";
import { toast } from "svelte-sonner";
import type { RuleOffer } from "./types";

export const remember = $state({ ask: null as { txId: string; category: string; offer: RuleOffer; reload: () => void } | null });
let timer: ReturnType<typeof setTimeout>;

export function askRemember(txId: string, category: string, offer: RuleOffer, reload: () => void): void {
  toast.dismiss();
  remember.ask = { txId, category, offer, reload };
  clearTimeout(timer);
  timer = setTimeout(closeRemember, 12000);
}

export function closeRemember(): void {
  clearTimeout(timer);
  remember.ask = null;
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
