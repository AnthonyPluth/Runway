<script lang="ts">
  import AcctLabel from "$lib/components/AcctLabel.svelte";
  import CategorySelect from "$lib/components/CategorySelect.svelte";
  import OrderDetail from "$lib/components/orders/OrderDetail.svelte";
  import { orderLabel } from "$lib/components/orders/retail";
  import { Badge } from "$lib/components/ui/badge";
  import { Button } from "$lib/components/ui/button";
  import { fmt, fmtDate } from "$lib/format";
  import { cn } from "$lib/utils";
  import RecurringPicker from "./RecurringPicker.svelte";
  import SplitEditor from "./SplitEditor.svelte";
  import type { RecurringItem, Tx } from "./types";

  // One transaction: its category saves as soon as you pick it. Under the row open the split editor and the
  // Amazon or Target order it paid for; ↻ links it to a recurring item.
  let { t, review, selected, recurring, onselect, onsave, onchanged }: {
    t: Tx; review: boolean; selected: boolean; recurring: RecurringItem[];
    onselect: (e: MouseEvent, checked: boolean) => void;
    onsave: (category: string) => Promise<void>;
    onchanged: () => void;
  } = $props();

  let saving = $state(false);
  let picking = $state(false);
  let splitting = $state(false);
  let showOrder = $state(false);

  const suggestion = $derived(!!t.needs_review && !!t.category && t.category_source === "ai");
  const linked = $derived((t.recurring_id ?? 0) > 0);
  const split = $derived(!!t.is_split && !!t.splits?.length);
  const name = $derived(t.payee || t.description || "");
  const initial = $derived(((t.payee || t.description || "?").replace(/^[^A-Za-z0-9]+/, "")[0] || "?").toUpperCase());
  // Shown on hover (and always on touch screens, and while focused), as in the classic list.
  const onHover = "opacity-0 group-hover:opacity-100 focus-visible:opacity-100 [@media(hover:none)]:opacity-100";

  async function save(category: string) {
    if (!category) return;
    saving = true;
    try { await onsave(category); } finally { saving = false; }
  }
</script>

<tr class={cn("group border-t align-middle", selected && "bg-muted/60")}>
  <td class="w-8 py-2 pl-3 pr-1">
    <input type="checkbox" class="size-4 cursor-pointer accent-primary align-middle" aria-label="Select this transaction" checked={selected}
      onclick={(e) => onselect(e, e.currentTarget.checked)} />
  </td>
  <td class="whitespace-nowrap px-2 py-2 text-muted-foreground tabular-nums">
    {fmtDate(t.posted)}{#if t.pending}<Badge variant="secondary" class="ml-1.5">pending</Badge>{/if}
  </td>
  <td class="min-w-64 px-2 py-2">
    <div class="flex flex-wrap items-center gap-1.5 font-medium">
      {#if t.logo}
        <img class="size-5 shrink-0 rounded-md bg-white object-contain" src={t.logo} alt="" loading="lazy" width="20" height="20" />
      {:else}
        <span class="flex size-5 shrink-0 items-center justify-center rounded-md bg-muted text-[11px] font-semibold text-muted-foreground" aria-hidden="true">{initial}</span>
      {/if}
      {name}
      {#if picking}
        <RecurringPicker {t} items={recurring} onclose={() => (picking = false)} {onchanged} />
      {:else}
        <button type="button" onclick={() => (picking = true)}
          title={linked ? `Recurring: ${t.recurring_name} (click to change)` : "Link to a recurring item"}
          aria-label={linked ? `Recurring: ${t.recurring_name} (click to change)` : "Link to a recurring item"}
          class={cn("cursor-pointer rounded px-1 text-[13px] font-normal text-muted-foreground hover:text-foreground",
            linked ? "bg-primary/15 font-semibold text-primary" : onHover)}>
          ↻{#if linked}<span class="ml-1 text-xs font-normal">{t.recurring_name}</span>{/if}
        </button>
      {/if}
      {#if t.retail}
        <button type="button" aria-expanded={showOrder} onclick={() => (showOrder = !showOrder)}
          title={`See what was in this ${t.retail.retailer === "amazon" ? "Amazon" : "Target"} order`}
          class="cursor-pointer rounded-md bg-secondary px-2 py-0.5 text-xs font-medium text-secondary-foreground hover:bg-primary/15 hover:text-primary">
          {orderLabel(t.retail)}</button>
      {/if}
    </div>
    <div class="max-w-[22rem] truncate text-xs text-muted-foreground" title={t.description ?? ""}>{t.description}</div>
    <div class="text-xs text-muted-foreground sm:hidden"><AcctLabel id={t.account_id} name={t.account_name ?? ""} /></div>
  </td>
  <td class="px-2 py-2 text-muted-foreground max-sm:hidden"><AcctLabel id={t.account_id} name={t.account_name ?? ""} /></td>
  <td class={cn("whitespace-nowrap px-2 py-2 text-right tabular-nums", t.amount > 0 && "font-semibold text-emerald-500")}>{fmt(t.amount)}</td>
  <td class="whitespace-nowrap px-2 py-2 pr-3">
    <div class="flex items-center gap-1.5">
      {#if split}
        <Badge class="bg-primary/15 text-primary">split</Badge>
        <span class="text-xs text-muted-foreground">
          {#each t.splits ?? [] as s, i (i)}{#if i}{" · "}{/if}<span title={s.note || undefined}>{s.category} {fmt(Math.abs(s.amount))}</span>{/each}
        </span>
      {:else}
        <CategorySelect value={t.category ?? ""} ghost={!review} disabled={saving} class="w-52" onchange={save} />
      {/if}
      {#if suggestion}
        <Badge class="bg-primary/15 text-primary" title="AI suggestion confidence">{Math.round((t.confidence || 0) * 100)}%</Badge>
        <Button variant="link" size="sm" class="h-auto px-1" title="Keep the suggested category" disabled={saving}
          onclick={() => save(t.category ?? "")}>✓ Keep</Button>
      {/if}
      {#if !review && t.needs_review}<Badge variant="outline" class="border-amber-500/50 text-amber-500">review</Badge>{/if}
      <Button variant="link" size="sm" class={cn("h-auto px-1", !split && onHover)} title="Spread this across several categories"
        onclick={() => (splitting = true)}>{split ? "Edit split" : "Split"}</Button>
    </div>
  </td>
</tr>
{#if splitting}
  <tr><td colspan="6" class="px-3 pb-3"><SplitEditor {t} onclose={() => (splitting = false)} onsaved={() => { splitting = false; onchanged(); }} /></td></tr>
{/if}
{#if showOrder && t.retail}
  <tr><td colspan="6" class="px-3 pb-3" data-editor><OrderDetail orderId={t.retail.order_id} onchange={onchanged} /></td></tr>
{/if}
