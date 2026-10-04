// Running a rule over past transactions, from the Rules list's Apply and from saving a rule with "Apply to past
// transactions" ticked: count what it would change (the preview), ask (RuleApplyDialog), run it, and offer Undo.
import { api } from "$lib/api";
import { refreshState } from "$lib/app.svelte";
import { fromRule, restoreTx, type RuleWas } from "$lib/components/transactions/restore";
import { plural } from "$lib/format";
import { undoable } from "$lib/undo";
import { toast } from "svelte-sonner";
import type { Rule, RulePreview } from "./types";
import { act, errMsg } from "$lib/act";

/** What the preview needs of a rule (saved, or as the editor has it). */
export type RuleBody = Pick<Rule, "match" | "match_mode" | "amount_min" | "amount_max" | "direction" | "account_id" | "category" | "rename" | "review" | "split">;
export const previewBody = (r: RuleBody): RuleBody => ({
  match: r.match, match_mode: r.match_mode, amount_min: r.amount_min, amount_max: r.amount_max, direction: r.direction,
  account_id: r.account_id, category: r.category, rename: r.rename, review: r.review, split: r.split,
});

/** What a rule does, in a few words: "Restaurants · rename to Chipotle · put in Review". */
export function ruleActions(r: Pick<Rule, "split" | "category" | "rename" | "review">): string {
  const bits: string[] = [];
  if (r.split) bits.push("split " + r.split.map((p) => `${p.percent}% ${p.category}`).join(", "));
  if (r.category) bits.push(r.category);
  if (r.rename) bits.push(`rename to ${r.rename}`);
  if (r.review) bits.push("put in Review");
  return bits.join(" · ");
}

/** How many past transactions the rule would match and change; null (with the reason in a toast) when it can't say. */
export async function countChanges(body: RuleBody): Promise<RulePreview | null> {
  try {
    const p = await api<RulePreview>("/api/rules/preview", { method: "POST", body: previewBody(body) });
    if (p.error) { toast.error(p.error); return null; }
    return p;
  } catch (err) { toast.error(errMsg(err)); return null; }
}

/** Why there's nothing to ask about. */
export const nothingToChange = (p: RulePreview) => p.matches
  ? `Nothing to change: the ${plural(p.matches, "past transaction")} it matches already ${p.matches === 1 ? "has" : "have"} what it does`
  : "No past transactions match this rule";

/** Run saved rule `id` over past transactions, and say how many changed, with Undo when the server kept what they were.
 *  `said` goes before the count ("Rule added · "). False (with the error shown) when it failed. */
export async function applyRule(id: number, said = ""): Promise<boolean> {
  return act(async () => {
    const res = await api<{ updated: number; changed: RuleWas[]; undoable: boolean }>(`/api/rules/${id}/apply`, { method: "POST" });
    const msg = `${said}${plural(res.updated, "transaction")} updated`;
    if (res.undoable && res.changed.length) undoable(msg, async () => { await restoreTx(res.changed.map(fromRule)); });
    else toast.success(msg, res.updated ? { description: "That’s too many to undo from here." } : undefined);
    refreshState();
  });
}

/** The question the dialog asks: how many it changes, what to (`what`), and what saying yes does. */
export class RuleApplyAsk {
  open = $state(false);
  changes = $state(0);
  what = $state("");
  go: () => Promise<boolean> = async () => false;

  ask(changes: number, what: string, go: () => Promise<boolean>): void {
    this.changes = changes; this.what = what; this.go = go; this.open = true;
  }
}
