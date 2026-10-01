<script lang="ts">
  import AcctLabel from "$lib/components/AcctLabel.svelte";
  import CategorySelect from "$lib/components/CategorySelect.svelte";
  import CatIcon from "$lib/components/CatIcon.svelte";
  import OrderDetail from "$lib/components/orders/OrderDetail.svelte";
  import { orderLabel } from "$lib/components/orders/retail";
  import { Badge } from "$lib/components/ui/badge";
  import { Button } from "$lib/components/ui/button";
  import { app } from "$lib/app.svelte";
  import { fmt, fmtDate } from "$lib/format";
  import { cn } from "$lib/utils";
  import ChevronDown from "@lucide/svelte/icons/chevron-down";
  import type { RecurringItem } from "$lib/components/recurring/types";
  import RecurringPicker from "./RecurringPicker.svelte";
  import LogoPicker from "./LogoPicker.svelte";
  import Logo from "$lib/components/Logo.svelte";
  import SplitEditor from "./SplitEditor.svelte";
  import type { Tx } from "./types";
  import { openOrders } from "./expanded.svelte";
  import Receipt from "@lucide/svelte/icons/receipt";
  import Clock from "@lucide/svelte/icons/clock";
  import Flag from "@lucide/svelte/icons/flag";

  // One transaction: its category saves as soon as you pick it. Under the row open the split editor, and (from its
  // receipt badge) the Amazon or Target order it was matched to; ↻ links it to a recurring item. On a phone the category sits under the
  // merchant; on a wider screen it has a column of its own. From lg up a row is one 40px line (logo, merchant, category,
  // account, amount in aligned columns), the same height opened or not, and a chevron opens the details: the account with its
  // institution and the bank's own text.
  let { t, review, selected, selecting, recurring, onselect, onsave, onchanged }: {
    t: Tx; review: boolean; selected: boolean; selecting: boolean; recurring: RecurringItem[];
    onselect: (e: MouseEvent, checked: boolean) => void;
    onsave: (category: string) => Promise<void>;
    onchanged: () => void;
  } = $props();

  let saving = $state(false);
  let picking = $state(false);
  let splitting = $state(false);
  let open = $state(false);
  const showOrder = $derived(openOrders.has(t.id));
  const store = $derived(t.retail?.retailer === "amazon" ? "Amazon" : t.retail?.retailer === "target" ? "Target" : "store");
  function toggleOrder() { if (showOrder) openOrders.delete(t.id); else openOrders.add(t.id); }

  const suggestion = $derived(!!t.needs_review && !!t.category && t.category_source === "ai");
  const linked = $derived((t.recurring_id ?? 0) > 0);
  const split = $derived(!!t.is_split && !!t.splits?.length);
  const name = $derived(t.payee || t.description || "");
  // The bank's own text, only when it says more than the merchant name does.
  const detail = $derived.by(() => {
    const d = (t.description ?? "").trim(), squash = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, "");
    return d && squash(d) !== squash(name) ? d : "";
  });
  const brand = $derived(app.state?.brands?.[t.account_id]);
  const sourceLabel = $derived(t.category_source === "manual" ? "You" : t.category_source === "ai" ? "AI suggestion" : t.category_source === "rule" ? "A rule" : t.category_source === "retail" ? "The store order" : t.category_source ?? "");
  const initial = $derived(((t.payee || t.description || "?").replace(/^[^A-Za-z0-9]+/, "")[0] || "?").toUpperCase());
  // Shown on hover (and always on touch screens, and while focused).
  const onHover = "opacity-0 group-hover:opacity-100 focus-visible:opacity-100 [@media(hover:none)]:opacity-100";

  async function save(category: string) {
    if (!category) return;
    saving = true;
    try { await onsave(category); } finally { saving = false; }
  }
</script>

<div role="listitem" data-tx={t.id} class={cn("group grid grid-cols-[auto_auto_minmax(0,1fr)_auto] items-center gap-y-1 px-4 py-2.5 md:grid-cols-[auto_auto_minmax(10rem,1.2fr)_minmax(11rem,1fr)_7.5rem] md:gap-y-0 md:px-4 lg:grid-cols-[auto_auto_minmax(12rem,1.2fr)_minmax(10rem,1.5fr)_minmax(6rem,1fr)_7.5rem_2.5rem] lg:grid-rows-[minmax(2.5rem,auto)] lg:py-0",
  selected ? "bg-primary/15" : "hover:bg-white/[0.03]")}>
  <label class={cn("col-start-1 row-span-2 mr-3 flex items-center self-center md:row-span-2 lg:row-span-1 lg:mr-2.5", !selecting && "max-md:hidden",
    !selecting && !selected && "md:opacity-0 md:group-hover:opacity-100 md:focus-within:opacity-100")}>
    <input type="checkbox" class="size-4 cursor-pointer accent-primary" aria-label={`Select ${name}`} checked={selected}
      onclick={(e) => onselect(e, e.currentTarget.checked)} />
  </label>

  <div class="col-start-2 row-span-2 mr-3 self-center md:row-span-2 lg:row-span-1 lg:mr-2.5">
    <LogoPicker name={t.payee || t.description || ""} {onchanged}>
      <!-- The logo as its brand draws it, with nothing behind it (Runway asks Logo.dev for its dark-background version,
           so a dark mark doesn't vanish on the dark page). -->
      {#if t.logo}
        <Logo src={t.logo} size={36} class="lg:size-5! lg:rounded" />
      {:else}
        <span class="flex size-9 items-center justify-center rounded-lg bg-muted text-sm font-semibold text-muted-foreground lg:size-5 lg:rounded lg:text-[11px]" aria-hidden="true">{initial}</span>
      {/if}
    </LogoPicker>
  </div>

  <div class="@container/title col-start-3 row-start-1 min-w-0 pr-3">
    <!-- The merchant keeps at least 6 characters. Beside it, the badges (receipt included) and the recurring name are their
         full text when the cell is 24rem wide (or, on a narrow phone, on a line of their own), and otherwise just an icon. -->
    <div class="flex min-w-0 items-center gap-1.5 max-sm:flex-wrap">
      <span class="min-w-[6ch] truncate font-medium max-sm:max-w-full" title={name}>{name}</span>
      {#if t.pending}<Badge variant="secondary" title="pending" class="shrink-0 px-1.5 @sm/title:px-2 max-sm:px-2">
        <Clock class="size-3 @sm/title:hidden max-sm:hidden" aria-label="pending" /><span class="hidden @sm/title:inline max-sm:inline">pending</span></Badge>{/if}
      {#if !review && t.needs_review}<Badge variant="outline" title="review" class="shrink-0 border-amber-500/50 px-1.5 text-amber-500 @sm/title:px-2 max-sm:px-2">
        <Flag class="size-3 @sm/title:hidden max-sm:hidden" aria-label="review" /><span class="hidden @sm/title:inline max-sm:inline">review</span></Badge>{/if}
      {#if picking}
        <RecurringPicker {t} items={recurring} onclose={() => (picking = false)} {onchanged} />
      {:else}
        <button type="button" onclick={() => (picking = true)}
          title={linked ? `Recurring: ${t.recurring_name} (click to change)` : "Link to a recurring item"}
          aria-label={linked ? `Recurring: ${t.recurring_name} (click to change)` : "Link to a recurring item"}
          class={cn("inline-flex shrink-0 cursor-pointer items-center rounded px-1 text-[13px] text-muted-foreground hover:text-foreground",
            linked ? "font-semibold text-primary @sm/title:min-w-0 @sm/title:shrink-[8] @sm/title:overflow-hidden @sm/title:bg-primary/15 max-lg:[@media(max-height:500px)]:bg-transparent!" : onHover)}>
          ↻{#if linked}<span class="ml-1 hidden truncate text-xs font-normal @sm/title:inline max-lg:[@media(max-height:500px)]:hidden!">{t.recurring_name}</span>{/if}
        </button>
      {/if}
      {#if t.retail}
        <button type="button" aria-expanded={showOrder} aria-controls={`order-${t.id}`} aria-label={orderLabel(t.retail)} onclick={toggleOrder}
          title={showOrder ? `Hide the ${store} order` : `Show what was in this ${store} order`}
          class={cn("inline-flex shrink-0 cursor-pointer items-center gap-1 rounded-md px-1.5 py-0.5 text-xs font-medium transition-colors @sm/title:px-2 max-sm:px-2",
            showOrder ? "bg-primary/15 text-primary" : "bg-secondary text-secondary-foreground hover:bg-primary/15 hover:text-primary")}>
          <Receipt class="size-3 shrink-0" aria-hidden="true" /><span class="hidden @sm/title:inline max-sm:inline">receipt</span></button>
      {/if}
    </div>
  </div>
  <!-- Account (and the bank's own text): under the merchant on a tablet, its own column from lg up (where the bank's text
       is in the details instead). They share the line: the account shrinks (to its logo and an ellipsis), and the bank's
       text only shows once the cell is 24rem wide, so it never lands on the account. -->
  <div class="@container/acct col-start-3 row-start-2 flex min-w-0 items-center gap-1.5 pr-3 text-xs text-muted-foreground max-md:hidden lg:contents">
    <span class="min-w-0 shrink-[4] lg:col-start-5 lg:row-start-1 lg:pr-3" title={t.account_name || undefined}><AcctLabel id={t.account_id} name={t.account_name ?? ""} iconClass="lg:hidden" labelClass="max-lg:[@media(max-height:500px)]:hidden" /></span>
    {#if detail}<span aria-hidden="true" class="hidden shrink-0 @sm/acct:inline lg:hidden! max-lg:[@media(max-height:500px)]:hidden!">·</span><span class="hidden min-w-0 flex-1 truncate @sm/acct:block lg:hidden! max-lg:[@media(max-height:500px)]:hidden!" title={detail}>{detail}</span>{/if}
  </div>

  <!-- Category: under the merchant on a phone, its own column on a wider screen. -->
  <div class="@container/cat col-start-3 row-start-2 flex min-w-0 flex-wrap items-center gap-1.5 pr-3 md:col-start-4 md:row-span-2 md:row-start-1 md:flex-nowrap lg:row-span-1">
    <!-- On a phone the account is only its bank's logo, ahead of the category. -->
    <span class="shrink-0 md:hidden" title={t.account_name || undefined}><AcctLabel id={t.account_id} name={t.account_name ?? ""} labelClass="hidden" /></span>
    {#if split}
      <button type="button" class="flex min-w-0 cursor-pointer items-center gap-1.5 text-left text-xs" title="Edit the split" onclick={() => (splitting = true)}>
        <span class="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5 text-muted-foreground">
          {#each t.splits ?? [] as s, i (i)}
            <span class="inline-flex items-center gap-1" title={s.note || undefined}><CatIcon name={s.category} size={16} />{s.category} {fmt(Math.abs(s.amount))}</span>
          {/each}
        </span>
      </button>
    {:else}
      <!-- The chip shows the category; the (invisible) native picker on top of it does the choosing. -->
      <span class={cn("relative inline-flex min-w-0 max-w-full items-center gap-1.5 rounded-full py-0.5 pr-2 pl-0.5 text-sm transition-colors",
        t.category ? "hover:bg-muted" : "border border-dashed border-amber-500/60 pl-2 text-amber-500 hover:bg-amber-500/10",
        "focus-within:ring-2 focus-within:ring-ring", saving && "opacity-60")}>
        {#if t.category}<CatIcon name={t.category} size={22} class="rounded-full" />{/if}
        <span class="truncate" title={t.category || undefined}>{#if t.category}{t.category}{:else}<span class="@[12rem]/cat:hidden">Category</span><span class="hidden @[12rem]/cat:inline">Choose category</span>{/if}</span>
        <ChevronDown class={cn("size-3.5 shrink-0 text-muted-foreground", onHover)} aria-hidden="true" />
        <CategorySelect value={t.category ?? ""} disabled={saving} label={`Category for ${name}`}
          class="absolute inset-0 h-full w-full cursor-pointer opacity-0" onchange={save} />
      </span>
    {/if}
    {#if suggestion}
      <Badge class="bg-primary/15 text-primary" title="AI suggestion confidence">{Math.round((t.confidence || 0) * 100)}%</Badge>
      <Button variant="link" size="sm" class="h-auto px-1" title="Keep the suggested category" disabled={saving}
        onclick={() => save(t.category ?? "")}>✓ Keep</Button>
    {/if}
    {#if !split}
      <Button variant="link" size="sm" class={cn("h-auto shrink-0 px-1 text-xs text-muted-foreground max-md:hidden", onHover)}
        title="Spread this across several categories" onclick={() => (splitting = true)}>Split</Button>
    {/if}
  </div>

  <div class={cn("col-start-4 row-span-2 self-center whitespace-nowrap text-right tabular-nums md:col-start-5 md:row-span-2 lg:col-start-6 lg:row-span-1",
    t.amount > 0 ? "font-semibold text-emerald-500" : "font-medium")}>{fmt(t.amount)}</div>

  <button type="button" class={cn("col-start-7 row-start-1 hidden size-7 cursor-pointer justify-self-end items-center justify-center rounded text-muted-foreground hover:text-foreground lg:flex", !open && onHover)}
    aria-expanded={open} aria-controls={`detail-${t.id}`} aria-label={`Details for ${name}`} title={open ? "Hide the details" : "Show the details"} onclick={() => (open = !open)}>
    <ChevronDown class={cn("size-4 transition-transform motion-reduce:transition-none", open && "rotate-180")} aria-hidden="true" />
  </button>

  {#if open}
    <dl id={`detail-${t.id}`} class="col-span-full mb-2 hidden grid-cols-[repeat(auto-fit,minmax(11rem,1fr))] gap-x-6 gap-y-2 pl-[3.75rem] text-xs lg:grid">
      <div><dt class="text-muted-foreground">Account</dt><dd><AcctLabel id={t.account_id} name={t.account_name ?? ""} /></dd></div>
      {#if brand?.institution}<div><dt class="text-muted-foreground">Source</dt><dd>{brand.institution}</dd></div>{/if}
      <div><dt class="text-muted-foreground">Posted</dt><dd>{fmtDate(t.posted.slice(0, 10), { month: "short", day: "numeric", year: "numeric" })}</dd></div>
      {#if t.description}<div><dt class="text-muted-foreground">Bank’s text</dt><dd class="break-words">{t.description}</dd></div>{/if}
      {#if sourceLabel && t.category}<div><dt class="text-muted-foreground">Category set by</dt><dd>{sourceLabel}</dd></div>{/if}
    </dl>
  {/if}

  {#if splitting}
    <div class="col-span-full pt-2"><SplitEditor {t} onclose={() => (splitting = false)} onsaved={() => { splitting = false; onchanged(); }} /></div>
  {/if}
  {#if showOrder && t.retail}
    <div id={`order-${t.id}`} class="col-span-full pt-2 md:pl-[5.25rem]"><OrderDetail orderId={t.retail.order_id} onchange={onchanged} /></div>
  {/if}
</div>
