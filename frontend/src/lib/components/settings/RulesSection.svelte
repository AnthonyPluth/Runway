<script lang="ts" module>
  import type { Rule, SettingsAccount } from "./types";

  // The search box keeps what you typed when the page redraws.
  let rulesFilter = "";

  /** What a rule does, in a few words: "Restaurants · rename to Chipotle · put in Review". */
  export function ruleActions(r: Rule): string {
    const bits: string[] = [];
    if (r.split) bits.push("split " + r.split.map((p) => `${p.percent}% ${p.category}`).join(", "));
    if (r.category) bits.push(r.category);
    if (r.rename) bits.push(`rename to ${r.rename}`);
    if (r.review) bits.push("put in Review");
    return bits.join(" · ");
  }
</script>

<script lang="ts">
  import { api } from "$lib/api";
  import { refreshState, reload } from "$lib/app.svelte";
  import ConfirmButton from "$lib/components/ConfirmButton.svelte";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { ConfirmDialog } from "$lib/components/ui/confirm-dialog";
  import { fromRule, restoreTx, type RuleWas } from "$lib/components/transactions/restore";
  import { plural } from "$lib/format";
  import { undoable } from "$lib/undo";
  import { toast } from "svelte-sonner";
  import RuleEditor from "./RuleEditor.svelte";
  import type { RulePreview } from "./types";
  import { inputCls } from "./ui";

  // Settings → Rules: what each rule matches → what it does, with Edit / Apply / Remove, and a new-rule editor
  // (already open when there are no rules yet). Apply says how many past transactions it would change (the editor's
  // preview) and asks first; afterwards its toast can undo it.
  let { rules, accounts }: { rules: Rule[]; accounts: SettingsAccount[] } = $props();

  // svelte-ignore state_referenced_locally
  let adding = $state(!rules.length);
  let editing = $state<number | null>(null);
  let filter = $state(rulesFilter);
  const q = $derived(filter.trim().toLowerCase());
  $effect(() => { rulesFilter = q; });
  const hay = (r: Rule) => (r.summary + " " + ruleActions(r)).toLowerCase();

  let asking = $state(false);
  let pending = $state<{ rule: Rule; changes: number } | null>(null);

  async function apply(r: Rule) {
    try {
      const p = await api<RulePreview>("/api/rules/preview", { method: "POST", body: {
        match: r.match, match_mode: r.match_mode, amount_min: r.amount_min, amount_max: r.amount_max, direction: r.direction,
        account_id: r.account_id, category: r.category, rename: r.rename, review: r.review, split: r.split } });
      if (p.error) toast.error(p.error);
      else if (!p.changes) toast(p.matches ? `Nothing to change: the ${plural(p.matches, "past transaction")} it matches already ${p.matches === 1 ? "has" : "have"} what it does` : "No past transactions match this rule");
      else { pending = { rule: r, changes: p.changes }; asking = true; }
    } catch (err) { toast.error((err as Error).message); }
  }
  async function run(r: Rule): Promise<boolean> {
    try {
      const res = await api<{ updated: number; changed: RuleWas[]; undoable: boolean }>(`/api/rules/${r.id}/apply`, { method: "POST" });
      const said = `${plural(res.updated, "transaction")} updated`;
      if (res.undoable && res.changed.length) undoable(said, async () => { await restoreTx(res.changed.map(fromRule)); });
      else toast.success(said, res.updated ? { description: "That’s too many to undo from here." } : undefined);
      refreshState();
      return true;
    } catch (err) { toast.error((err as Error).message); return false; }
  }
  async function remove(id: number) {
    try { await api(`/api/rules/${id}`, { method: "DELETE" }); toast.success("Rule removed"); reload(); }
    catch (err) { toast.error((err as Error).message); }
  }
</script>

<Card.Root>
  <Card.Header>
    <Card.Title>Rules</Card.Title>
    <Card.Action><Button variant="outline" size="sm" onclick={() => (adding = !adding)}>Add</Button></Card.Action>
  </Card.Header>
  <Card.Content class="flex flex-col gap-4">
    {#if adding}<RuleEditor rule={null} {accounts} onclose={() => (adding = false)} />{/if}
    {#if rules.length}
      <input class={`${inputCls} w-full sm:max-w-sm`} type="search" placeholder={`Search ${rules.length} rules`} aria-label="Search rules" bind:value={filter} />
      <div class="flex flex-col">
        {#each rules as r (r.id)}
          {#if !q || hay(r).includes(q)}
            <div class="border-b last:border-b-0">
              <div class="group flex flex-wrap items-center gap-x-3 gap-y-1 py-2 md:flex-nowrap">
                <span class="min-w-0 basis-full text-sm md:basis-auto md:flex-[0_1_360px]">{r.summary || "any transaction"}</span>
                <span class="text-muted-foreground" aria-hidden="true">→</span>
                <span class="min-w-0 text-sm">{ruleActions(r)}</span>
                <span class="ml-auto flex shrink-0 items-center md:opacity-0 md:group-hover:opacity-100 md:group-focus-within:opacity-100">
                  <Button variant="link" size="sm" aria-expanded={editing === r.id} onclick={() => (editing = editing === r.id ? null : r.id!)}>Edit</Button>
                  <Button variant="link" size="sm" title="Run this rule over past transactions (not ones you categorized yourself)" onclick={() => apply(r)}>Apply</Button>
                  <ConfirmButton confirm="Remove?" onconfirm={() => remove(r.id!)}>Remove</ConfirmButton>
                </span>
              </div>
              {#if editing === r.id}<div class="pb-3"><RuleEditor rule={r} {accounts} onclose={() => (editing = null)} /></div>{/if}
            </div>
          {/if}
        {/each}
      </div>
    {/if}
  </Card.Content>
</Card.Root>

{#if pending}
  <ConfirmDialog bind:open={asking} title="Apply this rule to past transactions?" confirmLabel={`Change ${plural(pending.changes, "transaction")}`} busyLabel="Applying…"
    description={`This changes ${plural(pending.changes, "transaction")}: ${pending.rule.summary || "any transaction"} → ${ruleActions(pending.rule)}. Categories you picked yourself stay as they are. You can undo it afterwards.`}
    onconfirm={() => run(pending!.rule)} />
{/if}
