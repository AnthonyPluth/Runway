<script lang="ts">
  import { isPayingKind } from "$lib/accounts";
  import { categories } from "$lib/categories.svelte";
  import CategorySelect from "$lib/components/CategorySelect.svelte";
  import { Button } from "$lib/components/ui/button";
  import Group from "$lib/components/ui/group/Group.svelte";
  import { accountName, type Account, type Category } from "$lib/types";
  import { addCategory } from "./categories";
  import CategoryRow from "./CategoryRow.svelte";
  import { checkCls, fieldCls, inputCls, rowCls } from "./ui";

  // Settings → Categories: add one (at the top level or under another), and rename, move or remove each. They're
  // listed by kind, as the category pickers group them (a subcategory is always its parent's kind). A spending
  // category also picks the card or account its spending goes on, from the cards and bank accounts you can see.
  let { accounts = [] }: { accounts?: Account[] } = $props();
  const payAccounts = $derived(accounts.filter((a) => !a.hidden && isPayingKind(a.kind))
    .map((a) => ({ id: a.id, name: accountName(a), kind: a.kind }))
    .sort((a, b) => Number(b.kind === "credit") - Number(a.kind === "credit") || a.name.localeCompare(b.name)));
  let adding = $state(false);
  let name = $state("");
  let parent = $state("");
  let isTransfer = $state(false);
  let isIncome = $state(false);
  let nameInput = $state<HTMLInputElement | null>(null);

  const add = () => addCategory(name, parent || null, isTransfer, isIncome);
  async function open() { adding = !adding; await Promise.resolve(); nameInput?.focus(); }

  const KINDS: [string, (c: Category) => boolean][] = [
    ["Spending", (c) => !c.is_transfer && !c.is_income], ["Money in", (c) => !!c.is_income], ["Not spending", (c) => !!c.is_transfer]];
  const groups = $derived(KINDS.map(([title, test]) => ({ title, items: categories.list.filter(test) })).filter((g) => g.items.length));
</script>

{#snippet addButton()}
  <Button variant="ghost" size="sm" class="text-primary" aria-expanded={adding} onclick={open}>Add</Button>
{/snippet}

{#if adding}
  <div class={`${rowCls} tile`}>
    <label class={`${fieldCls} w-full sm:w-56`}>Name<input class={inputCls} bind:this={nameInput} bind:value={name}
      onkeydown={(e) => { if (e.key === "Enter") add(); if (e.key === "Escape") adding = false; }} /></label>
    <label class={`${fieldCls} w-full sm:w-64`}>Subcategory of
      <CategorySelect bind:value={parent} blank="— none (top level) —" canHoldChildren label="Subcategory of" class="w-full" />
    </label>
    <!-- A subcategory takes its parent's kind. -->
    <label class={`${checkCls} h-9 items-center [&>input]:mt-0`}><input type="checkbox" bind:checked={isTransfer} disabled={!!parent} /> Not spending (a transfer)</label>
    <label class={`${checkCls} h-9 items-center [&>input]:mt-0`}><input type="checkbox" bind:checked={isIncome} disabled={!!parent} /> Money in</label>
    <span class="flex gap-2">
      <Button onclick={add}>Add</Button>
      <Button variant="outline" onclick={() => (adding = false)}>Cancel</Button>
    </span>
  </div>
{/if}
{#each groups as g, i (g.title)}
  <Group title={g.title} inset="3.75rem" action={i === 0 ? addButton : undefined}>
    {#each g.items as c (c.name)}<CategoryRow {c} {payAccounts} />{/each}
  </Group>
{/each}
