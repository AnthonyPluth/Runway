<script lang="ts" module>
  import type { Rule, SettingsAccount } from "./types";
  import { ruleActions } from "./ruleApply.svelte";

  // The search box keeps what you typed when the page redraws.
  let rulesFilter = "";

</script>

<script lang="ts">
  import { api } from "$lib/api";
  import { reload } from "$lib/app.svelte";
  import { Button } from "$lib/components/ui/button";
  import Group from "$lib/components/ui/group/Group.svelte";
  import { undoable } from "$lib/undo";
  import { toast } from "svelte-sonner";
  import RuleApplyDialog from "./RuleApplyDialog.svelte";
  import RuleEditor from "./RuleEditor.svelte";
  import { RuleApplyAsk, applyRule, countChanges, nothingToChange, previewBody } from "./ruleApply.svelte";
  import { dangerGhost, inputCls } from "./ui";

  // Settings → Rules: what each rule matches → what it does, with Edit / Apply / Remove, and a new-rule editor
  // (already open when there are no rules yet). Apply says how many past transactions it would change (the editor's
  // preview) and asks first; afterwards its toast can undo it. Remove happens at once, with an Undo that adds the
  // rule back.
  let { rules, accounts }: { rules: Rule[]; accounts: SettingsAccount[] } = $props();

  // svelte-ignore state_referenced_locally
  let adding = $state(!rules.length);
  let editing = $state<number | null>(null);
  let filter = $state(rulesFilter);
  const q = $derived(filter.trim().toLowerCase());
  $effect(() => { rulesFilter = q; });
  const hay = (r: Rule) => (r.summary + " " + ruleActions(r)).toLowerCase();

  const asking = new RuleApplyAsk();
  async function apply(r: Rule) {
    const p = await countChanges(r);
    if (!p) return;
    if (!p.changes) toast(nothingToChange(p));
    else asking.ask(p.changes, `${r.summary || "any transaction"} → ${ruleActions(r)}`, () => applyRule(r.id!));
  }
  async function remove(r: Rule) {
    try { await api(`/api/rules/${r.id}`, { method: "DELETE" }); }
    catch (err) { toast.error((err as Error).message); return; }
    undoable("Rule removed", async () => {
      await api("/api/rules", { method: "POST", body: { ...previewBody(r), split: r.split ?? null, apply: false } });
      reload();
    });
    reload();
  }
  const actionCls = "h-8 px-2.5 text-muted-foreground hover:text-foreground";
</script>

<p class="-mt-2 text-sm text-muted-foreground" title="More conditions first; then “is exactly” before “starts with” before “contains”, and longer text first. Each kind of action (category, rename, Review) comes from the most specific rule that has one.">
  When several rules match, the most specific one wins.</p>

{#if adding}<RuleEditor rule={null} {accounts} onclose={() => (adding = false)} />{/if}

{#snippet addButton()}
  <Button variant="ghost" size="sm" class="text-primary" aria-expanded={adding} onclick={() => (adding = !adding)}>Add</Button>
{/snippet}

<Group title="Rules" action={addButton}>
  {#if rules.length}
    <div class="cell py-2">
      <input class={`${inputCls} w-full sm:max-w-sm`} type="search" placeholder={`Search ${rules.length} rules`} aria-label="Search rules" bind:value={filter} />
    </div>
  {:else}
    <p class="cell text-sm text-muted-foreground">No rules yet.</p>
  {/if}
  {#each rules as r (r.id)}
    {#if !q || hay(r).includes(q)}
      <div class="cell group flex-col items-stretch gap-0 py-1.5">
        <div class="flex min-h-9 flex-wrap items-center gap-x-3 gap-y-1 md:flex-nowrap">
          <span class="min-w-0 basis-full text-sm md:basis-auto md:flex-[0_1_360px]">{r.summary || "any transaction"}</span>
          <span class="text-muted-foreground" aria-hidden="true">→</span>
          <span class="min-w-0 text-sm">{ruleActions(r)}</span>
          <span class="ml-auto flex shrink-0 items-center hoverable:md:opacity-0 hoverable:md:group-hover:opacity-100 hoverable:md:group-focus-within:opacity-100">
            <Button variant="ghost" size="sm" class={actionCls} aria-expanded={editing === r.id} onclick={() => (editing = editing === r.id ? null : r.id!)}>Edit</Button>
            <Button variant="ghost" size="sm" class={actionCls} title="Run this rule over past transactions (not ones you categorized yourself)" onclick={() => apply(r)}>Apply</Button>
            <Button variant="ghost" size="sm" class={`h-8 px-2.5 ${dangerGhost}`} onclick={() => remove(r)}>Remove</Button>
          </span>
        </div>
        {#if editing === r.id}<div class="pt-1 pb-2"><RuleEditor rule={r} {accounts} onclose={() => (editing = null)} /></div>{/if}
      </div>
    {/if}
  {/each}
</Group>

<RuleApplyDialog q={asking} />
