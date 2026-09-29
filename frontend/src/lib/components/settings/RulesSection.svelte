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
  import { toast } from "svelte-sonner";
  import RuleEditor from "./RuleEditor.svelte";
  import { helpCls, inputCls } from "./ui";

  // Settings → Rules: what each rule matches → what it does, with Edit / Apply / Remove, and a new-rule editor
  // (already open when there are no rules yet).
  let { rules, accounts }: { rules: Rule[]; accounts: SettingsAccount[] } = $props();

  // svelte-ignore state_referenced_locally
  let adding = $state(!rules.length);
  let editing = $state<number | null>(null);
  let filter = $state(rulesFilter);
  const q = $derived(filter.trim().toLowerCase());
  $effect(() => { rulesFilter = q; });
  const hay = (r: Rule) => (r.summary + " " + ruleActions(r)).toLowerCase();

  async function apply(id: number) {
    try {
      const r = await api<{ updated: number }>(`/api/rules/${id}/apply`, { method: "POST" });
      toast.success(`${r.updated} transaction${r.updated === 1 ? "" : "s"} updated`); refreshState();
    } catch (err) { toast.error((err as Error).message); }
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
    <p class={helpCls}>When a transaction matches everything a rule asks for, the rule acts on it. Rules run on new transactions as
      they sync; <b class="text-foreground">Apply</b> runs one over past ones too (it won't change a category you picked yourself). The most specific rule wins.</p>
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
                  <Button variant="link" size="sm" title="Run this rule over past transactions (not ones you categorized yourself)" onclick={() => apply(r.id!)}>Apply</Button>
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
