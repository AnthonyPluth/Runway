// The one-click actions of the Churning page (check a plan off, mark a card credit used, snooze a to-do). Each says
// what changed in a toast with an Undo, so the same wording and undo work wherever the button is: in Upcoming, on a
// card's row, or in its edit area.
import { api } from "$lib/api";
import { fmt } from "$lib/format";
import { toast } from "svelte-sonner";
import { fullDate } from "./churning";
import type { PlanResult } from "./types";

const post = <T>(path: string, body: unknown = {}) => api<T>(`/api/churning/${path}`, { method: "POST", body });
const fail = (err: unknown) => toast.error((err as Error).message);

/** Check a card's plan off; the toast lists what changed (the card closed or changed, a new card added) and can undo it. */
export async function planDone(cardId: number, onchanged: () => void): Promise<void> {
  try {
    const r = await post<PlanResult>(`cards/${cardId}/plan/done`);
    toast("Done", { description: r.changes.join(". "), action: { label: "Undo", onClick: () => planUndo(cardId, onchanged) } });
    onchanged();
  } catch (err) { fail(err); }
}

export async function planUndo(cardId: number, onchanged: () => void): Promise<void> {
  try {
    const r = await post<PlanResult>(`cards/${cardId}/plan/undo`);
    toast("Undone", { description: r.changes.join(". ") });
    onchanged();
  } catch (err) { fail(err); }
}

/** Mark a benefit used: `amount` dollars of a credit, or (left out) the rest of this period's. */
export async function benefitUse(id: number, name: string, amount: number | string | null | undefined, onchanged: () => void): Promise<void> {
  try {
    const amt = amount == null || amount === "" ? null : Number(amount);
    const r = await post<{ id: number }>(`benefits/${id}/use`, amt == null ? {} : { amount: amt });
    toast(`Marked ${name} used${amt != null ? ` (${fmt(amt)})` : ""}`, {
      action: { label: "Undo", onClick: () => benefitUnuse(id, r.id, onchanged) },
    });
    onchanged();
  } catch (err) { fail(err); }
}

/** Undo a use: the one given, else the latest this period. */
export async function benefitUnuse(id: number, useId: number | null, onchanged: () => void): Promise<void> {
  try {
    await post(`benefits/${id}/unuse`, useId == null ? {} : { use_id: useId });
    toast("Undone");
    onchanged();
  } catch (err) { fail(err); }
}

/** Leave a to-do out of Upcoming for a while; the toast can bring it back. */
export async function taskSnooze(id: number, days: number, onchanged: () => void): Promise<void> {
  try {
    const r = await post<{ snooze_until: string | null }>(`tasks/${id}/snooze`, { days });
    toast(r.snooze_until ? `Snoozed until ${fullDate(r.snooze_until)}` : "Snoozed", {
      action: { label: "Undo", onClick: async () => { try { await post(`tasks/${id}/snooze`, {}); onchanged(); } catch (err) { fail(err); } } },
    });
    onchanged();
  } catch (err) { fail(err); }
}
