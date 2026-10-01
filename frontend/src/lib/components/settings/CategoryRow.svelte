<script lang="ts">
  import { api } from "$lib/api";
  import { refreshState, reload } from "$lib/app.svelte";
  import { CAT_MAX_DEPTH, catLabel, categories, categoryGroups } from "$lib/categories.svelte";
  import CategorySelect from "$lib/components/CategorySelect.svelte";
  import { Badge } from "$lib/components/ui/badge";
  import { Button } from "$lib/components/ui/button";
  import type { Category } from "$lib/types";
  import { cn } from "$lib/utils";
  import { toast } from "svelte-sonner";
  import { addCategory } from "./categories";
  import LookPicker from "./LookPicker.svelte";
  import { inputCls, selectCls } from "./ui";

  // A category: its name (rename it in place, unless it's built in), its kind, how many transactions use it, and
  // + Sub / Move / Remove, each of which swaps the actions for a small form.
  let { c }: { c: Category } = $props();
  const name = $derived(c.name);
  const builtIn = $derived(!!c.protected);
  let mode = $state<"" | "sub" | "move" | "remove">("");

  async function rename(e: Event) {
    const f = e.currentTarget as HTMLInputElement;
    try { await api("/api/categories/rename", { method: "POST", body: { name, new_name: f.value } }); toast.success("Renamed"); reload(); }
    catch (err) { toast.error((err as Error).message); f.value = name; }
  }

  // + Sub
  let subName = $state("");
  const addSub = () => addCategory(subName, name, false, false);

  // Move: only under parents this branch fits beneath (it and its subcategories must stay within CAT_MAX_DEPTH levels).
  let moveTo = $state("");
  const height = $derived(1 + Math.max(0, ...categories.list.filter((x) => x.path.includes(name)).map((x) => x.path.length - c.path.length)));
  async function move() {
    const parent = moveTo || null;
    try {
      await api("/api/categories/move", { method: "POST", body: { name, parent } });
      toast.success(parent ? `Moved ${name} under ${parent}` : `${name} is now a top-level category`);
      await refreshState(); reload();
    } catch (err) { toast.error((err as Error).message); }
  }

  // Remove: its transactions go to Review, or to another category.
  let sendTo = $state("");
  const others = $derived(categoryGroups().flatMap((g) => g.items));
  async function remove() {
    try {
      const r = await api<{ moved?: number }>("/api/categories/remove", { method: "POST", body: { name, move_to: sendTo || null } });
      toast.success(r.moved ? `Removed · ${r.moved} transaction${r.moved === 1 ? "" : "s"} ${sendTo ? "moved" : "sent to Review"}` : "Removed");
      await refreshState(); reload();
    } catch (err) { toast.error((err as Error).message); }
  }
  function start(m: typeof mode) { mode = m; moveTo = c.parent || ""; subName = ""; sendTo = ""; }
  const focus = (el: HTMLElement) => { el.focus(); };
</script>

<div class="group flex min-h-11 flex-wrap items-center gap-x-2.5 gap-y-1 border-b py-1 pr-1 last:border-b-0"
  style:padding-left={`${4 + (c.depth || 0) * 22}px`}>
  <span class="flex min-w-0 flex-1 basis-48 flex-wrap items-center gap-x-1.5 gap-y-1">
    <LookPicker {c} />
    {#if builtIn}
      <span class={cn("px-3 text-sm",c.parent && "text-muted-foreground")}>{name}</span>
    {:else}
      <input class={cn(inputCls, "h-8 w-full max-w-64 border-transparent shadow-none dark:bg-transparent hover:border-input focus-visible:border-ring", c.parent && "text-muted-foreground")}
        value={name} aria-label="Category name" onchange={rename} />
    {/if}
    {#if c.is_transfer}<Badge variant="secondary">not spending</Badge>{:else if c.is_income}<Badge variant="secondary">money in</Badge>{/if}
    {#if builtIn}<Badge variant="secondary">built-in</Badge>{/if}
  </span>
  <span class="w-10 text-right text-sm text-muted-foreground tabular-nums" title="Transactions">{c.transactions || ""}</span>
  <span class={cn("flex flex-wrap items-center justify-end gap-1",
    !mode && "md:w-52 md:opacity-0 md:group-hover:opacity-100 md:group-focus-within:opacity-100")}>
    {#if mode === "sub"}
      <input class={cn(inputCls, "h-8 w-56")} placeholder={`New subcategory of ${name}`} aria-label={`New subcategory of ${name}`} bind:value={subName}
        use:focus onkeydown={(e) => { if (e.key === "Enter") addSub(); if (e.key === "Escape") mode = ""; }} />
      <Button size="sm" onclick={addSub}>Add</Button>
      <Button variant="link" size="sm" onclick={() => (mode = "")}>Cancel</Button>
    {:else if mode === "move"}
      <CategorySelect bind:value={moveTo} blank="Top level" label={`Move ${name} under`} class="h-8 w-56"
        exclude={(x) => x.path.includes(name) || x.path.length + height > CAT_MAX_DEPTH} />
      <Button size="sm" onclick={move}>Move</Button>
      <Button variant="link" size="sm" onclick={() => (mode = "")}>Cancel</Button>
    {:else if mode === "remove"}
      {#if c.transactions}
        <select class={cn(selectCls, "h-8 w-64")} aria-label="Move transactions to" bind:value={sendTo}>
          <option value="">Send its {c.transactions} transaction{c.transactions === 1 ? "" : "s"} to Review</option>
          <optgroup label="Or move them to">
            {#each others as o (o.name)}<option value={o.name} disabled={o.name === name}>{catLabel(o)}</option>{/each}
          </optgroup>
        </select>
      {/if}
      <Button size="sm" onclick={remove}>Remove {name}</Button>
      <Button variant="link" size="sm" onclick={() => (mode = "")}>Cancel</Button>
    {:else}
      {#if (c.depth || 0) < CAT_MAX_DEPTH - 1}<Button variant="link" size="sm" onclick={() => start("sub")}>+ Sub</Button>{/if}
      {#if !builtIn}
        <Button variant="link" size="sm" onclick={() => start("move")}>Move</Button>
        <Button variant="link" size="sm" onclick={() => start("remove")}>Remove</Button>
      {/if}
    {/if}
  </span>
</div>
