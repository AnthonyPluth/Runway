<script lang="ts">
  import { api } from "$lib/api";
  import { refreshState, reload } from "$lib/app.svelte";
  import { autosave } from "$lib/autosave";
  import { CAT_MAX_DEPTH, catLabel, categories, categoryGroups, loadCategories } from "$lib/categories.svelte";
  import CategorySelect from "$lib/components/CategorySelect.svelte";
  import { Badge } from "$lib/components/ui/badge";
  import { Button } from "$lib/components/ui/button";
  import { ConfirmDialog } from "$lib/components/ui/confirm-dialog";
  import { plural } from "$lib/format";
  import type { Category } from "$lib/types";
  import { undoable } from "$lib/undo";
  import { cn } from "$lib/utils";
  import { toast } from "svelte-sonner";
  import Ellipsis from "@lucide/svelte/icons/ellipsis";
  import FolderInput from "@lucide/svelte/icons/folder-input";
  import Plus from "@lucide/svelte/icons/plus";
  import Trash from "@lucide/svelte/icons/trash";
  import { addCategory } from "./categories";
  import LookPicker from "./LookPicker.svelte";
  import type { PayAccount } from "$lib/components/budget/types";
  import { dangerGhost, inputCls, selectCls } from "./ui";

  // A category: its emoji, its name (rename it in place, unless it's built in), how many transactions use it, and
  // + Sub / Move / Remove: icons that show on hover (always, on a touch screen), behind "…" on a phone. + Sub and Move
  // open a small form under the row. Remove asks first when anything uses the category, saying what happens to it;
  // when nothing does, it's removed straight away with an Undo.
  // A spending category also has the card or account its spending goes on (the budget forecast spends its budget
  // there): a quiet select after its name, showing the one chosen, or "Automatic" with the one that means.
  let { c, payAccounts = [] }: { c: Category; payAccounts?: PayAccount[] } = $props();
  const name = $derived(c.name);
  const builtIn = $derived(!!c.protected);
  let mode = $state<"" | "menu" | "sub" | "move">("");
  const canSub = $derived((c.depth || 0) < CAT_MAX_DEPTH - 1);

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

  // Remove. In use (transactions, rules, a budget or order items): ask, and the transactions go to Review or to
  // another category. Not in use: it goes now, and Undo adds it back as it was (its place, kind and emoji).
  let asking = $state(false);
  let sendTo = $state("");
  const others = $derived(categoryGroups().flatMap((g) => g.items));
  const inUse = $derived(!!(c.transactions || c.rules || c.budgeted || c.items));
  function askRemove() {
    mode = "";
    if (inUse) { sendTo = ""; asking = true; } else removeNow();
  }
  async function removeNow() {
    const was = { name, parent: c.parent || null, is_transfer: !!c.is_transfer, is_income: !!c.is_income, icon: c.custom_icon ?? null, color: c.custom_color ?? null };
    try { await api("/api/categories/remove", { method: "POST", body: { name, move_to: null } }); }
    catch (err) { toast.error((err as Error).message); return; }
    undoable(`Removed ${name}`, async () => {
      await api("/api/categories", { method: "POST", body: { name: was.name, parent: was.parent, is_transfer: was.is_transfer, is_income: was.is_income } });
      if (was.icon || was.color) await api("/api/categories/look", { method: "POST", body: { name: was.name, icon: was.icon ?? "", color: was.color ?? "" } });
      await refreshState(); reload();
    });
    await refreshState(); reload();
  }
  async function removeAsked(): Promise<boolean> {
    try {
      const r = await api<{ moved?: number }>("/api/categories/remove", { method: "POST", body: { name, move_to: sendTo || null } });
      toast.success(r.moved ? `Removed ${name} · ${plural(r.moved, "transaction")} ${sendTo ? `moved to ${sendTo}` : "sent to Review"}` : `Removed ${name}`);
      await refreshState(); reload();
      return true;
    } catch (err) { toast.error((err as Error).message); return false; }
  }
  // What else goes with it, one short line each.
  const consequences = $derived.by(() => {
    const out: string[] = [];
    if (c.rules) out.push(sendTo ? `${plural(c.rules, "rule")} will set ${sendTo} instead.` : `${plural(c.rules, "rule")} ${c.rules === 1 ? "stops" : "stop"} setting it (a rule left with nothing to do is removed).`);
    if (c.budgeted) out.push("Its budget is deleted.");
    if (c.items) out.push(sendTo ? `${plural(c.items, "order item")} ${c.items === 1 ? "moves" : "move"} to ${sendTo}.` : `${plural(c.items, "order item")} will be categorized again automatically.`);
    return out;
  });

  // Paid with. Automatic is the nearest parent's choice, else the account used most for it.
  const spending = $derived(!c.is_transfer && !c.is_income);
  const acctName = (id: string | null | undefined) => (id ? payAccounts.find((x) => x.id === id)?.name : undefined);
  const inherited = $derived(c.path.slice(0, -1).reverse().map((p) => categories.list.find((x) => x.name === p)).find((x) => x?.pay_with));
  const automatic = $derived.by(() => {
    const via = acctName(inherited?.pay_with);
    if (via) return `Automatic (${via}, as ${inherited!.name})`;
    const usual = acctName(c.usual_account);
    return usual ? `Automatic (usually ${usual})` : "Automatic";
  });
  async function savePayWith(f: HTMLSelectElement) {
    await api("/api/categories/pay-with", { method: "POST", body: { name, pay_with: f.value } });
    await loadCategories();   // its subcategories' "Automatic" follows it
  }

  function start(m: typeof mode) { mode = m; moveTo = c.parent || ""; subName = ""; }
  const focus = (el: HTMLElement) => { el.focus(); };
  const iconBtn = "size-8 text-muted-foreground hover:text-foreground";
</script>

{#snippet payWith(cls: string)}
  <select class={cn(selectCls, "h-8 w-72 max-w-full truncate text-sm md:text-xs hover:border-input dark:bg-transparent phone:h-11 phone:w-full", !c.pay_with && "text-muted-foreground", cls)}
    value={c.pay_with ?? ""} aria-label={`Account ${name} is paid with`} title="Which card or account this spending goes on; the forecast uses it"
    use:autosave={(f) => savePayWith(f as HTMLSelectElement)}>
    <option value="">{automatic}</option>
    <optgroup label="Cards">
      {#each payAccounts.filter((x) => x.kind === "credit") as x (x.id)}<option value={x.id}>{x.name}</option>{/each}
    </optgroup>
    <optgroup label="Bank accounts">
      {#each payAccounts.filter((x) => x.kind !== "credit") as x (x.id)}<option value={x.id}>{x.name}</option>{/each}
    </optgroup>
  </select>
{/snippet}

<div class="cell group flex-wrap gap-y-1.5 py-1.5" style:padding-left={`${16 + (c.depth || 0) * 22}px`}>
  <LookPicker {c} />
  <span class="flex min-w-0 flex-1 flex-wrap items-center gap-x-1.5 gap-y-0.5 phone:flex-col phone:flex-nowrap phone:items-start">
    {#if builtIn}
      <span class={cn("flex h-8 items-center px-3 text-sm phone:h-auto", c.parent && "text-muted-foreground")}>{name}</span>
      <Badge variant="secondary" class="phone:ml-3" title="Runway uses it itself, so it can’t be renamed, moved or removed">built-in</Badge>
    {:else}
      <input style:width={c.depth ? `calc(16rem - ${c.depth * 22}px)` : undefined} class={cn(inputCls, "h-8 w-64 max-w-full border-transparent shadow-none dark:bg-transparent hover:border-input focus-visible:border-ring", c.parent && "text-muted-foreground")}
        value={name} aria-label="Category name" onchange={rename} />
    {/if}
    {#if spending && payAccounts.length}{@render payWith("phone:hidden")}{/if}
  </span>
  <span class="w-10 shrink-0 text-right text-sm text-muted-foreground tabular-nums" title={c.transactions ? plural(c.transactions, "transaction") : undefined}>
    {#if c.transactions}<span class="sr-only">{plural(c.transactions, "transaction")}</span><span aria-hidden="true">{c.transactions}</span>{/if}
  </span>
  {#if canSub || !builtIn}
    <span class={cn("flex w-26 shrink-0 flex-nowrap items-center justify-end phone:hidden",
      !mode && "hoverable:opacity-0 hoverable:group-hover:opacity-100 hoverable:group-focus-within:opacity-100")}>
      {#if canSub}<Button variant="ghost" size="icon" class={iconBtn} title="Add a subcategory" aria-label={`Add a subcategory to ${name}`}
        aria-expanded={mode === "sub"} onclick={() => start(mode === "sub" ? "" : "sub")}><Plus /></Button>{/if}
      {#if !builtIn}
        <Button variant="ghost" size="icon" class={iconBtn} title="Move" aria-label={`Move ${name}`} aria-expanded={mode === "move"}
          onclick={() => start(mode === "move" ? "" : "move")}><FolderInput /></Button>
        <Button variant="ghost" size="icon" class={cn(iconBtn, dangerGhost)} title={c.has_children ? "Remove or move its subcategories first" : "Remove"}
          aria-label={`Remove ${name}`} disabled={!!c.has_children} onclick={askRemove}><Trash /></Button>
      {/if}
    </span>
    <Button variant="ghost" size="icon" class={cn(iconBtn, "desktop:hidden")} aria-label={`Actions for ${name}`} aria-expanded={mode !== ""}
      onclick={() => (mode = mode ? "" : "menu")}><Ellipsis /></Button>
  {/if}

  {#if mode}
    <div class="flex basis-full flex-wrap items-center gap-2 pb-1 pl-11">
      {#if mode === "menu"}
        <!-- On a phone the paid-with account is here, under "…", so each row stays one line. -->
        {#if spending && payAccounts.length}<label class="flex w-full flex-col gap-1 text-xs text-muted-foreground desktop:hidden">Paid with{@render payWith("")}</label>{/if}
        {#if canSub}<Button variant="outline" size="sm" onclick={() => start("sub")}>+ Subcategory</Button>{/if}
        {#if !builtIn}
          <Button variant="outline" size="sm" onclick={() => start("move")}>Move</Button>
          <Button variant="ghost" size="sm" class={dangerGhost} disabled={!!c.has_children} onclick={askRemove}>Remove</Button>
        {/if}
      {:else if mode === "sub"}
        <input class={cn(inputCls, "h-8 w-56")} placeholder={`New subcategory of ${name}`} aria-label={`New subcategory of ${name}`} bind:value={subName}
          use:focus onkeydown={(e) => { if (e.key === "Enter") addSub(); if (e.key === "Escape") mode = ""; }} />
        <Button size="sm" onclick={addSub}>Add</Button>
      {:else if mode === "move"}
        <CategorySelect bind:value={moveTo} blank="Top level" label={`Move ${name} under`} class="h-8 w-56"
          exclude={(x) => x.path.includes(name) || x.path.length + height > CAT_MAX_DEPTH} />
        <Button size="sm" onclick={move}>Move</Button>
      {/if}
      <Button variant="link" size="sm" onclick={() => (mode = "")}>Cancel</Button>
    </div>
  {/if}
</div>

<ConfirmDialog bind:open={asking} title={`Remove ${name}?`} confirmLabel="Remove" busyLabel="Removing…" destructive onconfirm={removeAsked}>
  {#snippet description()}
    {#if c.transactions}
      <label class="flex flex-col gap-1.5">
        <span>Its {plural(c.transactions ?? 0, "transaction")}:</span>
        <select class={cn(selectCls, "w-full border-input")} bind:value={sendTo}>
          <option value="">Go to Review, uncategorized</option>
          <optgroup label="Or move them to">
            {#each others as o (o.name)}<option value={o.name} disabled={o.name === name}>{catLabel(o)}</option>{/each}
          </optgroup>
        </select>
      </label>
    {/if}
    {#each consequences as line (line)}<p>{line}</p>{/each}
  {/snippet}
</ConfirmDialog>
