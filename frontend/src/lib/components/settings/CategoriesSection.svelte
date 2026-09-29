<script lang="ts">
  import { categories } from "$lib/categories.svelte";
  import CategorySelect from "$lib/components/CategorySelect.svelte";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { addCategory } from "./categories";
  import CategoryRow from "./CategoryRow.svelte";
  import { checkCls, fieldCls, inputCls, rowCls } from "./ui";

  // Settings → Categories: add one (at the top level or under another), and rename, move or remove each.
  let adding = $state(false);
  let name = $state("");
  let parent = $state("");
  let isTransfer = $state(false);
  let isIncome = $state(false);
  let nameInput = $state<HTMLInputElement | null>(null);

  const add = () => addCategory(name, parent || null, isTransfer, isIncome);
  async function open() { adding = true; await Promise.resolve(); nameInput?.focus(); }
</script>

<Card.Root>
  <Card.Header>
    <Card.Title>Categories</Card.Title>
    <Card.Action><Button variant="outline" size="sm" onclick={open}>Add</Button></Card.Action>
  </Card.Header>
  <Card.Content>
    {#if adding}
      <div class={`${rowCls} mb-4 rounded-lg border p-3`}>
        <label class={`${fieldCls} w-full sm:w-56`}>Name<input class={inputCls} bind:this={nameInput} bind:value={name}
          onkeydown={(e) => e.key === "Enter" && add()} /></label>
        <label class={`${fieldCls} w-full sm:w-64`}>Subcategory of
          <CategorySelect bind:value={parent} blank="— none (top level) —" canHoldChildren label="Subcategory of" class="w-full" />
        </label>
        <!-- A subcategory takes its parent's kind. -->
        <label class={`${checkCls} h-9 items-center [&>input]:mt-0`}><input type="checkbox" bind:checked={isTransfer} disabled={!!parent} /> Not spending (a transfer)</label>
        <label class={`${checkCls} h-9 items-center [&>input]:mt-0`}><input type="checkbox" bind:checked={isIncome} disabled={!!parent} /> Money in</label>
        <Button onclick={add}>Add</Button>
      </div>
    {/if}
    <div class="flex flex-col">
      {#each categories.list as c (c.name)}<CategoryRow {c} />{/each}
    </div>
  </Card.Content>
</Card.Root>
