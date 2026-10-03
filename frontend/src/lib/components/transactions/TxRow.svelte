<script lang="ts">
  import AcctLabel from "$lib/components/AcctLabel.svelte";
  import BankBadge from "$lib/components/BankBadge.svelte";
  import CategorySelect from "$lib/components/CategorySelect.svelte";
  import OrderDetail from "$lib/components/orders/OrderDetail.svelte";
  import { orderLabel } from "$lib/components/orders/retail";
  import { Badge } from "$lib/components/ui/badge";
  import { Button } from "$lib/components/ui/button";
  import { fmt, fmtSigned } from "$lib/format";
  import { categories } from "$lib/categories.svelte";
  import { cn } from "$lib/utils";
  import ChevronDown from "@lucide/svelte/icons/chevron-down";
  import ChevronRight from "@lucide/svelte/icons/chevron-right";
  import ArrowLeftRight from "@lucide/svelte/icons/arrow-left-right";
  import SplitIcon from "@lucide/svelte/icons/split";
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
  import Repeat from "@lucide/svelte/icons/repeat";

  // One transaction: its category saves as soon as you pick it. Under the row open the split editor, and (from its
  // receipt badge) the Amazon or Target order it was matched to; the repeat icon links it to a recurring item. On a phone the category sits under the
  // merchant; on a wider screen it has a column of its own. From lg up a row is one 40px line (logo, merchant, category,
  // account, amount in aligned columns). The merchant's name and the chevron open its details (`onopen`: the sheet,
  // where it can be edited).
  // `family`: under a category filter, that category and its subcategories. A split transaction then shows only its part
  // in them (`t.match`): that amount, a picker that changes only that part, and a receipt of just those items.
  // `oneAccount`: the list is filtered to one account, so the row leaves it out (the details still name it).
  let { t, review, selected, selecting, recurring, family, oneAccount = false, onselect, onsave, onchanged, onopen }: {
    t: Tx; review: boolean; selected: boolean; selecting: boolean; recurring: RecurringItem[]; family?: string[]; oneAccount?: boolean;
    onselect: (e: MouseEvent, checked: boolean) => void;
    /** Saves the category; false when it couldn't be (the picker then shows the saved one again). */
    onsave: (category: string) => Promise<boolean | void>;
    onchanged: () => void;
    onopen?: () => void;
  } = $props();

  let saving = $state(false);
  let picking = $state(false);
  let splitting = $state(false);
  let splitFrom = $state<HTMLElement | null>(null);   // what opened the split editor: focus goes back there when it closes
  function openSplit(e: MouseEvent) { splitFrom = e.currentTarget as HTMLElement; splitting = true; }
  const showOrder = $derived(openOrders.has(t.id));
  const store = $derived(t.retail?.retailer === "amazon" ? "Amazon" : t.retail?.retailer === "target" ? "Target" : "store");
  function toggleOrder() { if (showOrder) openOrders.delete(t.id); else openOrders.add(t.id); }

  const suggestion = $derived(!!t.needs_review && !!t.category && t.category_source === "ai");
  const linked = $derived((t.recurring_id ?? 0) > 0);
  const split = $derived(!!t.is_split && !!t.splits?.length);
  const part = $derived(split && t.match ? t.match : null);
  // A transfer between your own accounts (a card payment, say), not what's marked Ignore: a small ⇄ before the category.
  const transfer = $derived(!!t.category && t.category !== "Ignore" &&
    !!categories.list.find((c) => c.name === t.category && c.is_transfer && c.top !== "Ignore"));
  const name = $derived(t.payee || t.description || "");
  // What the category pickers show: the saved category, or the one just picked while it saves (and the saved one again
  // if that fails).
  let picked = $derived(t.category ?? "");
  let pickedPart = $derived(part?.categories.length === 1 ? part.categories[0] : "");
  // The bank's own text, only when it says more than the merchant name does.
  const detail = $derived.by(() => {
    const d = (t.description ?? "").trim(), squash = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, "");
    return d && squash(d) !== squash(name) ? d : "";
  });
  const initial = $derived(((t.payee || t.description || "?").replace(/^[^A-Za-z0-9]+/, "")[0] || "?").toUpperCase());
  // Shown on hover (and always on touch screens, which can't hover, and while the row has focus).
  const onHover = "hoverable:opacity-0 hoverable:group-hover:opacity-100 hoverable:group-focus-within:opacity-100";
  // Shown on hover only: a touch screen leaves them out (fewer icons on a phone), and has them in the details instead.
  const hoverOnly = "hoverable:opacity-0 hoverable:group-hover:opacity-100 hoverable:group-focus-within:opacity-100 [@media(hover:none)]:hidden";

  async function save(category: string) {
    if (!category) return;
    saving = true;
    try {
      if ((await onsave(category)) === false) { picked = t.category ?? ""; pickedPart = part?.categories.length === 1 ? part.categories[0] : ""; }
    } finally { saving = false; }
  }
</script>

<!-- The columns give way before the amount does: below lg the merchant and category shrink to fit, and the amount and
     the chevron always keep their width. -->
<div role="listitem" data-tx={t.id} class={cn("group grid grid-cols-[auto_auto_minmax(0,1fr)_auto_auto] items-center gap-y-1 py-2.5 pr-1 pl-4 md:grid-cols-[auto_auto_minmax(0,1.2fr)_minmax(0,1fr)_auto_auto] md:gap-y-0 lg:grid-cols-[auto_auto_minmax(12rem,1.2fr)_minmax(10rem,1.5fr)_minmax(6rem,1fr)_7.5rem_2.5rem] lg:grid-rows-[minmax(2.5rem,auto)] lg:py-0 lg:pr-4",
  selected ? "bg-primary/15" : "hover:bg-white/[0.03]")}>
  <label class={cn("col-start-1 row-span-2 mr-3 flex items-center self-center md:row-span-2 lg:row-span-1 lg:mr-2.5", !selecting && "max-md:hidden",
    !selecting && !selected && "hoverable:md:opacity-0 hoverable:md:group-hover:opacity-100 hoverable:md:group-focus-within:opacity-100")}>
    <input type="checkbox" class="size-4 cursor-pointer accent-primary" aria-label={`Select ${name}`} checked={selected}
      onclick={(e) => onselect(e, e.currentTarget.checked)} />
  </label>

  <!-- The account is a small badge on the merchant's logo, at every width (wider screens also name it, in its own column). -->
  <div class="relative col-start-2 row-span-2 mr-3 self-center md:row-span-2 lg:row-span-1 lg:mr-2.5">
    {#if !oneAccount}<BankBadge accountId={t.account_id} name={t.account_name ?? ""} />{/if}
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
      <button type="button" class="min-w-[6ch] cursor-pointer truncate text-left font-medium hover:underline hover:underline-offset-4 max-sm:max-w-full" title={name}
        aria-haspopup="dialog" onclick={() => onopen?.()}>{name}</button>
      {#if t.pending}<Badge variant="secondary" title="pending" class="shrink-0 px-1.5 @sm/title:px-2 max-sm:px-2">
        <Clock class="size-3 @sm/title:hidden max-sm:hidden" aria-label="pending" /><span class="hidden @sm/title:inline max-sm:inline">pending</span></Badge>{/if}
      {#if !review && t.needs_review}<Badge variant="outline" title="review" class="shrink-0 border-warning/50 px-1.5 text-warning @sm/title:px-2 max-sm:px-2">
        <Flag class="size-3 @sm/title:hidden max-sm:hidden" aria-label="review" /><span class="hidden @sm/title:inline max-sm:inline">review</span></Badge>{/if}
      {#if picking}
        <RecurringPicker {t} items={recurring} onclose={() => (picking = false)} {onchanged} />
      {:else}
        <button type="button" onclick={() => (picking = true)}
          title={linked ? `Recurring: ${t.recurring_name} (click to change)` : "Link to a recurring item"}
          aria-label={linked ? `Recurring: ${t.recurring_name} (click to change)` : "Link to a recurring item"}
          class={cn("inline-flex shrink-0 cursor-pointer items-center rounded px-1 text-[13px] text-muted-foreground hover:text-foreground",
            linked ? "font-semibold text-primary @sm/title:min-w-0 @sm/title:shrink-[8] @sm/title:overflow-hidden @sm/title:bg-primary/15 max-lg:[@media(max-height:500px)]:bg-transparent!" : hoverOnly)}>
          <Repeat class="size-3.5" aria-hidden="true" />{#if linked}<span class="ml-1 hidden truncate text-xs font-normal @sm/title:inline max-lg:[@media(max-height:500px)]:hidden!">{t.recurring_name}</span>{/if}
        </button>
      {/if}
      {#if t.retail}
        <button type="button" aria-expanded={showOrder} aria-controls={`order-${t.id}`} aria-label={`Receipt: ${orderLabel(t.retail)}`} onclick={toggleOrder}
          title={showOrder ? `Hide the ${store} order` : `Show what was in this ${store} order`}
          class={cn(
            // Big enough to find and hit: a 16px icon in a 28px button (the row is 40px), tinted so it stands out from the
            // gray badges, and a click area reaching a little past it on every side.
            "relative inline-flex min-h-7 min-w-7 shrink-0 cursor-pointer items-center justify-center gap-1 rounded-md px-1.5 text-xs font-medium transition-colors @sm/title:px-2 max-sm:px-2",
            "after:absolute after:-inset-1.5 after:content-['']",
            showOrder ? "bg-primary/25 text-primary" : "bg-primary/12 text-primary hover:bg-primary/25")}>
          <Receipt class="size-4 shrink-0" aria-hidden="true" /><span class="hidden @sm/title:inline max-sm:inline">receipt</span></button>
      {/if}
    </div>
  </div>
  <!-- Account (and the bank's own text): under the merchant on a tablet, its own column from lg up (where the bank's text
       is in the details instead). They share the line: the account shrinks (to its logo and an ellipsis), and the bank's
       text only shows once the cell is 24rem wide, so it never lands on the account. -->
  <div class="@container/acct col-start-3 row-start-2 flex min-w-0 items-center gap-1.5 pr-3 text-xs text-muted-foreground max-md:hidden lg:contents">
    {#if !oneAccount}<span class="min-w-0 shrink-[4] lg:col-start-5 lg:row-start-1 lg:pr-3" title={t.account_name || undefined}><AcctLabel id={t.account_id} name={t.account_name ?? ""} iconClass="hidden" labelClass="max-lg:[@media(max-height:500px)]:hidden" /></span>{/if}
    {#if detail}{#if !oneAccount}<span aria-hidden="true" class="hidden shrink-0 @sm/acct:inline lg:hidden! max-lg:[@media(max-height:500px)]:hidden!">·</span>{/if}<span class="hidden min-w-0 flex-1 truncate @sm/acct:block lg:hidden! max-lg:[@media(max-height:500px)]:hidden!" title={detail}>{detail}</span>{/if}
  </div>

  <!-- Category: under the merchant on a phone, its own column on a wider screen. -->
  <div class="@container/cat col-start-3 row-start-2 flex min-w-0 flex-wrap items-center gap-1.5 pr-3 md:col-start-4 md:row-span-2 md:row-start-1 md:flex-nowrap lg:row-span-1">
    {#if part}
      <!-- Only the part in the filter's category: picking another changes just that part. -->
      <span class={cn("relative inline-flex min-w-0 max-w-full items-center gap-1.5 rounded-full py-0.5 pr-2 pl-2 text-sm transition-colors hover:bg-muted focus-within:ring-2 focus-within:ring-ring", saving && "opacity-60")}>
        <span class="truncate" title={part.categories.join(", ")}>{part.categories.join(", ")}</span>
        <ChevronDown class={cn("size-3.5 shrink-0 text-muted-foreground", hoverOnly)} aria-hidden="true" />
        <CategorySelect bind:value={pickedPart} disabled={saving} label={`Category for the ${part.categories.join(", ")} part of ${name}`}
          class="absolute inset-0 h-full w-full cursor-pointer opacity-0" onchange={save} />
      </span>
      <Button variant="link" size="sm" class="h-auto shrink-0 px-1 text-xs text-muted-foreground" title="Edit the whole split" onclick={(e) => openSplit(e)}>split</Button>
    {:else if split}
      <button type="button" class="flex min-w-0 cursor-pointer items-center gap-1.5 text-left text-xs" title="Edit the split" onclick={(e) => openSplit(e)}>
        <!-- Below lg just how many parts; the full run of them from lg up. -->
        <span class="inline-flex items-center gap-1 text-muted-foreground lg:hidden" title={(t.splits ?? []).map((s) => `${s.category} ${fmt(Math.abs(s.amount))}`).join(", ")}>
          <SplitIcon class="size-3.5 shrink-0" aria-hidden="true" />{(t.splits ?? []).length} parts</span>
        <span class="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5 text-muted-foreground max-lg:hidden">
          {#each t.splits ?? [] as s, i (i)}
            <span class="inline-flex items-center gap-1" title={s.note || undefined}>{s.category} {fmt(Math.abs(s.amount))}</span>
          {/each}
        </span>
      </button>
    {:else}
      <!-- The chip shows the category; the (invisible) native picker on top of it does the choosing. -->
      <span class={cn("relative inline-flex min-w-0 max-w-full items-center gap-1.5 rounded-full py-0.5 pr-2 pl-2 text-sm transition-colors",
        t.category ? "hover:bg-muted" : "border border-dashed border-warning/60 pl-2 text-warning hover:bg-warning/10",
        "focus-within:ring-2 focus-within:ring-ring", saving && "opacity-60")}>
        {#if transfer}<ArrowLeftRight class="size-3.5 shrink-0 text-muted-foreground" aria-label="Transfer" />{/if}
        <span class="truncate" title={t.category || undefined}>{#if t.category}{t.category}{:else}<span class="@[12rem]/cat:hidden">Category</span><span class="hidden @[12rem]/cat:inline">Choose category</span>{/if}</span>
        <ChevronDown class={cn("size-3.5 shrink-0 text-muted-foreground", hoverOnly)} aria-hidden="true" />
        <CategorySelect bind:value={picked} disabled={saving} label={`Category for ${name}`}
          class="absolute inset-0 h-full w-full cursor-pointer opacity-0" onchange={save} />
      </span>
    {/if}
    {#if suggestion}
      <Badge class="bg-primary/15 text-primary" title="AI suggestion confidence">{Math.round((t.confidence || 0) * 100)}%</Badge>
      <Button variant="link" size="sm" class="h-auto px-1" title="Keep the suggested category" disabled={saving}
        onclick={() => save(t.category ?? "")}>✓ Keep</Button>
    {/if}
    {#if !split}
      <Button variant="link" size="sm" class={cn("h-auto shrink-0 px-1 text-xs text-muted-foreground max-lg:hidden", onHover)}
        title="Spread this across several categories" onclick={(e) => openSplit(e)}>Split</Button>
    {/if}
  </div>

  <div class={cn("col-start-4 row-span-2 self-center whitespace-nowrap text-right tabular-nums md:col-start-5 md:row-span-2 lg:col-start-6 lg:row-span-1",
    (part?.amount ?? t.amount) > 0 ? "font-semibold text-good" : "font-medium", t.pending && "opacity-70")}>
    {#if part}{fmtSigned(part.amount)}<span class="block text-[11px] leading-tight font-normal text-muted-foreground" title="The whole transaction">of {fmt(Math.abs(t.amount))}</span>
    {:else}{fmtSigned(t.amount)}{/if}</div>

  <button type="button" class={cn("col-start-5 row-span-2 ml-1 flex size-7 cursor-pointer items-center justify-center justify-self-end self-center rounded text-muted-foreground hover:text-foreground phone:size-11 phone:-my-2 md:col-start-6 lg:col-start-7 lg:row-span-1 lg:row-start-1 lg:ml-0",
    "lg:hoverable:opacity-0 lg:hoverable:group-hover:opacity-100 lg:hoverable:group-focus-within:opacity-100")}
    aria-haspopup="dialog" aria-label={`Details for ${name}`} title="Details" onclick={() => onopen?.()}>
    <ChevronRight class="size-4" aria-hidden="true" />
  </button>

  {#if splitting}
    <div class="col-span-full pt-2"><SplitEditor {t} returnFocus={splitFrom} onclose={() => (splitting = false)} onsaved={() => { splitting = false; onchanged(); }} /></div>
  {/if}
  {#if showOrder && t.retail}
    <div id={`order-${t.id}`} class="col-span-full pt-2 md:pl-[5.25rem]"><OrderDetail orderId={t.retail.order_id} family={part ? family : undefined} onchange={onchanged} /></div>
  {/if}
</div>
