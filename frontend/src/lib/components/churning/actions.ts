// The one-click actions of the Churning page (check a plan off or a to-do, mark a card credit used, snooze a to-do, drop a
// planned item, take a balance off). Each says what changed in a toast with an Undo, so the same wording and undo work
// wherever the button is: in Upcoming, on a card's row, or in its edit area. What can't be put back (a card, a benefit,
// a bank bonus or a planned item deleted for good) asks first in a ConfirmDialog instead.
import { api } from "$lib/api";
import { fmt } from "$lib/format";
import { undoable } from "$lib/undo";
import { toast } from "svelte-sonner";
import { fullDate } from "./churning";
import type { PlanResult } from "./types";

const post = <T>(path: string, body: unknown = {}) => api<T>(`/api/churning/${path}`, { method: "POST", body });
const fail = (err: unknown) => toast.error((err as Error).message);

/** Check a card's plan off; the toast lists what changed (the card closed or changed, a new card added) and can undo it. */
export async function planDone(cardId: number, onchanged: () => void): Promise<void> {
  try {
    const r = await post<PlanResult>(`cards/${cardId}/plan/done`);
    undoable("Done", () => planUndone(cardId, onchanged), { description: r.changes.join(". ") });
    onchanged();
  } catch (err) { fail(err); }
}

// The undo itself, for the toast (which shows a failure); planUndo is the same from a button.
async function planUndone(cardId: number, onchanged: () => void): Promise<string> {
  const r = await post<PlanResult>(`cards/${cardId}/plan/undo`);
  onchanged();
  return r.changes.join(". ");
}

export async function planUndo(cardId: number, onchanged: () => void): Promise<void> {
  try { toast("Undone", { description: await planUndone(cardId, onchanged) }); } catch (err) { fail(err); }
}

/** Mark a benefit used: `amount` dollars of a credit, or (left out) the rest of this period's. */
export async function benefitUse(id: number, name: string, amount: number | string | null | undefined, onchanged: () => void): Promise<void> {
  try {
    const amt = amount == null || amount === "" ? null : Number(amount);
    const r = await post<{ id: number }>(`benefits/${id}/use`, amt == null ? {} : { amount: amt });
    undoable(`Marked ${name} used${amt != null ? ` (${fmt(amt)})` : ""}`, () => unuse(id, r.id, onchanged));
    onchanged();
  } catch (err) { fail(err); }
}

/** Take a use off (the one given, else this period's latest); the toast puts it back, the same amount on the same day. */
export async function benefitUnuse(id: number, name: string, use: { id: number; amount_used: number | null; used_on: string } | null, onchanged: () => void): Promise<void> {
  try {
    await unuse(id, use?.id ?? null, onchanged);
    undoable(`Took the use off ${name}`, async () => {
      await post(`benefits/${id}/use`, { used_on: use?.used_on, ...(use?.amount_used != null ? { amount: use.amount_used } : {}) });
      onchanged();
    });
  } catch (err) { fail(err); }
}
async function unuse(id: number, useId: number | null, onchanged: () => void): Promise<void> {
  await post(`benefits/${id}/unuse`, useId == null ? {} : { use_id: useId });
  onchanged();
}

/** Check a to-do off; the toast brings it back. */
export async function taskDone(id: number, title: string, onchanged: () => void): Promise<void> {
  try {
    await post(`tasks/${id}`, { done: true });
    undoable("Done", async () => { await post(`tasks/${id}`, { done: false }); onchanged(); }, { description: title });
    onchanged();
  } catch (err) { fail(err); }
}

/** Drop a planned item; the toast wants it again (as it was: wanted or ready). */
export async function wishDrop(id: number, name: string, was: string, onchanged: () => void): Promise<void> {
  try {
    await post(`wishlist/${id}`, { status: "dropped" });
    undoable(`Dropped ${name}`, async () => { await post(`wishlist/${id}`, { status: was }); onchanged(); });
    onchanged();
  } catch (err) { fail(err); }
}

/** Take a balance off the list; the toast puts back the points and the day they were entered. */
export async function balanceRemove(owner: string, currency: string, name: string, was: { points: number; as_of: string | null | undefined }, onchanged: () => void): Promise<void> {
  try {
    await post("balances", { owner, currency, points: null });
    undoable(`Removed ${owner}’s ${name} balance`, async () => {
      await post("balances", { owner, currency, points: was.points, ...(was.as_of ? { as_of: was.as_of } : {}) });
      onchanged();
    });
    onchanged();
  } catch (err) { fail(err); }
}

/** Leave a to-do out of Upcoming for a while; the toast can bring it back. */
export async function taskSnooze(id: number, days: number, onchanged: () => void): Promise<void> {
  try {
    const r = await post<{ snooze_until: string | null }>(`tasks/${id}/snooze`, { days });
    undoable(r.snooze_until ? `Snoozed until ${fullDate(r.snooze_until)}` : "Snoozed", async () => { await post(`tasks/${id}/snooze`, {}); onchanged(); });
    onchanged();
  } catch (err) { fail(err); }
}
