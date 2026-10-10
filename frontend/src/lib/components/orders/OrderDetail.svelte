<script lang="ts">
  import { api } from "$lib/api";
  import { catLook, loadCategories } from "$lib/categories.svelte";
  import { cn } from "$lib/utils";
  import AiButton from "$lib/components/AiButton.svelte";
  import CategorySelect from "$lib/components/CategorySelect.svelte";
  import { Button } from "$lib/components/ui/button";
  import { Badge } from "$lib/components/ui/badge";
  import { fmt, fmtDate } from "$lib/format";
  import { undoable } from "$lib/undo";
  import { toast } from "svelte-sonner";
  import { STORES, STORE_SITES, type RetailOrder } from "./retail";
  import { act, errMsg } from "$lib/act";

  // An Amazon or Target order: its items, each with a category you can change (remembered for the next time you
  // buy it), and the card charges it was paid with. `onchange` runs after anything here changes a transaction.
  // Call loadCategories() before showing it.
  // `family`: from a list filtered by a category, that category and its subcategories. Only their items show (all of them
  // a click away), with an estimate of their share of the tax and shipping, as the transaction's part counts it.
  let { orderId, family, onchange }: { orderId: string; family?: string[]; onchange?: () => void } = $props();
  let showAll = $state(false);
  // Their share of the tax and shipping (less any discount): the order's total over its items, in proportion.
  function extras(o: RetailOrder, sum: number): string {
    if (o.total == null || !o.subtotal || o.subtotal <= 0) return "+ tax & shipping";
    const share = sum * (o.total - o.subtotal) / o.subtotal;
    return share >= 0.005 ? `+ ~${fmt(share)} tax & shipping` : "";
  }

  // The order stays on screen while it loads again after a change, so its rows update where they are.
  let order = $state<RetailOrder | null>(null);
  let failed = $state("");
  let picking = $state<Record<string, Promise<Candidate[]> | undefined>>({});
  type Candidate = { id: string; posted: string; amount: number; payee?: string; description?: string; account_name?: string };

  async function load() {
    try { order = await api<RetailOrder>(`/api/retail/orders/${encodeURIComponent(orderId)}`, { keep: true }); failed = ""; }
    catch (err) { failed = errMsg(err); }
  }
  load();
  function changed() { onchange?.(); picking = {}; load(); }

  async function post<T = unknown>(path: string, body: unknown, msg: string | ((r: T) => string)) {
    await act(async () => {
      const r = await api<T>(path, { method: "POST", body });
      toast.success(typeof msg === "string" ? msg : msg(r));
      changed();
    });
  }
  // A change with an Undo: the reply's `was`, sent back to `restore` (the charge's or the item's), puts back exactly
  // what was there, the transactions it changed included.
  async function undoablePost(path: string, restore: string, body: unknown, msg: (r: { was: unknown; orders?: number }) => string) {
    await act(async () => {
      const r = await api<{ was: unknown; orders?: number }>(path, { method: "POST", body });
      undoable(msg(r), async () => { await api(restore, { method: "POST", body: { was: r.was } }); changed(); });
      changed();
    });
  }
  // The AI's suggestions for items that have no category, from the button above the items: nothing is saved until you use
  // one (a suggested new category is created then).
  type Suggestion = { item_id: number; category: string | null; new_category: { name: string; parent: string | null } | null };
  let suggestions = $state<Record<number, Suggestion>>({});
  let asking = $state(false);
  async function suggest() {
    asking = true;
    await act(async () => {
      const list = await api<Suggestion[]>(`/api/retail/orders/${encodeURIComponent(orderId)}/suggest`, { method: "POST" });
      suggestions = Object.fromEntries(list.map((s) => [s.item_id, s]));
      if (!list.length) toast("The AI had nothing to suggest");
    });
    asking = false;
  }
  async function useSuggestion(s: Suggestion) {
    await act(async () => {
      const r = await api<{ category: string; created: boolean; orders: number }>(`/api/retail/items/${s.item_id}`,
        { method: "POST", body: s.new_category ? { new_category: s.new_category } : { category: s.category } });
      if (r.created) await loadCategories();
      toast.success(r.created ? `Created ${r.category} and saved` : "Saved");
      delete suggestions[s.item_id];
      changed();
    });
  }
  const charge = (id: string, what: string) => `/api/retail/charges/${encodeURIComponent(id)}/${what}`;
  function pick(id: string) { picking[id] = api<Candidate[]>(charge(id, "candidates"), { keep: true }).catch(() => []); }
</script>

<div class="flex flex-col gap-3 rounded-lg bg-muted/40 p-4 text-sm">
  {#if failed && !order}
    <p class="text-muted-foreground" role="alert">{failed} <Button variant="link" size="sm" class="h-auto p-0" onclick={load}>Retry</Button></p>
  {:else if !order}
    <div class="h-16 animate-pulse motion-reduce:animate-none rounded-md bg-muted" aria-busy="true"></div>
  {:else}
    {@const o = order}
    {#if failed}<p class="text-xs text-muted-foreground" role="alert">Couldn’t refresh this order · <Button variant="link" size="sm" class="h-auto p-0 text-xs" onclick={load}>Retry</Button></p>{/if}
    {@const totals = [o.subtotal != null ? `items ${fmt(o.subtotal)}` : "", o.shipping ? `shipping ${fmt(o.shipping)}` : "",
      o.tax != null ? `tax ${fmt(o.tax)}` : ""].filter(Boolean).join(" · ")}
    <div class="flex flex-wrap items-baseline gap-x-3 gap-y-1">
      <b>{STORES[o.retailer] || o.retailer} {o.channel === "store" ? "purchase" : "order"} {o.order_number}</b>
      <span class="text-xs text-muted-foreground tabular-nums">
        {o.placed ? fmtDate(o.placed, { month: "short", day: "numeric", year: "numeric" }) : ""}{o.total != null ? ` · ${fmt(o.total)}` : ""}{totals ? ` (${totals})` : ""}{o.payment ? ` · ${o.payment}` : ""}
      </span>
      <a class="ml-auto text-xs font-medium underline underline-offset-4" href={o.url} target="_blank" rel="noopener">
        Open on {STORE_SITES[o.retailer] || STORES[o.retailer] || o.retailer}</a>
    </div>

    {#if o.items.length}
      {#if o.items.some((x) => !x.category)}
        <div class="flex items-center gap-2">
          <AiButton busy={asking} busyLabel="Suggesting…" label="Suggest categories" onclick={suggest}
            title="For the items with no category; it can propose a new one" />
        </div>
      {/if}
      {@const mine = family?.length ? o.items.filter((x) => x.category && family.includes(x.category)) : []}
      {@const items = mine.length && !showAll ? mine : o.items}
      <div class="flex flex-col">
        {#each items as i (i.id)}
          <!-- The item and its price are the line; the category is a small chip under the item (icon, then name; it opens the
               picker), with a suggestion beside it when the AI has one. -->
          <div class="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-3 gap-y-0.5 border-t border-border/50 py-1.5 first:border-t-0">
            <span class="truncate font-medium text-foreground" title={i.title}>{#if i.quantity > 1}<span class="mr-0.5 font-normal text-muted-foreground tabular-nums">{i.quantity}×</span> {/if}{i.title}</span>
            <span class="text-right font-medium tabular-nums">{fmt(i.amount)}</span>
            <div class="col-span-2 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
              <CategorySelect value={i.category ?? ""} label={`Category for ${i.title}`}
                class={cn("inline-flex min-h-6 max-w-full min-w-0 items-center gap-1 rounded-full py-0.5 pr-2 pl-1.5 text-xs transition-colors focus-visible:ring-2 focus-visible:ring-ring phone:min-h-8",
                  i.category ? "bg-muted text-muted-foreground hover:bg-muted/70" : "border border-dashed border-warning/60 text-warning hover:bg-warning/10")}
                onchange={(v) => v && undoablePost(`/api/retail/items/${i.id}`, `/api/retail/items/${i.id}/restore`, { category: v },
                  (r) => `${i.title} → ${v}${(r.orders ?? 0) > 1 ? ` · ${r.orders} orders` : ""}`)}>
                {#if i.category}<span aria-hidden="true">{catLook(i.category).icon}</span>{/if}
                <span class="truncate" title={i.category || undefined}>{i.category || "Choose category"}</span>
              </CategorySelect>
              {#if !i.category && suggestions[i.id]}
                {@const s = suggestions[i.id]}
                <Button size="sm" variant="outline" class="h-auto py-0.5 text-xs whitespace-normal" aria-label={`${s.new_category ? `Create ${s.new_category.name} and use it` : `Use ${s.category}`} for ${i.title}`}
                  onclick={() => useSuggestion(s)}>{s.new_category ? `New: ${s.new_category.name}` : `Use ${s.category}`}</Button>
              {/if}
            </div>
          </div>
        {/each}
      </div>
      <!-- One of the few lines spelled out: without it a pick here looks like it changes only this order. -->
      <p class="text-xs text-muted-foreground">A category picked for an item applies to it in every order.</p>
      {#if mine.length}
        <div class="flex flex-wrap items-center gap-x-3 text-xs text-muted-foreground">
          {#if !showAll}<span class="tabular-nums">{extras(o, mine.reduce((n, x) => n + x.amount, 0))}</span>{/if}
          {#if mine.length < o.items.length}
            <Button variant="link" size="sm" class="h-auto p-0 text-xs" onclick={() => (showAll = !showAll)}>
              {showAll ? `Only the ${family![0]} items` : `Show all ${o.items.length} items`}</Button>
          {/if}
        </div>
      {/if}
    {:else}
      <p class="text-muted-foreground">{o.details ? "No items in this order." : "Runway hasn't read this order's items yet; they come with the next import."}</p>
    {/if}

    <div class="flex flex-col gap-2 border-t pt-3">
      {#each o.charges as c (c.id)}
        <div class="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span class="text-muted-foreground tabular-nums">{c.amount > 0 ? "Refund" : "Charged"} {fmtDate(c.date)} · {fmt(Math.abs(c.amount))}{c.payment ? ` · ${c.payment}` : ""}</span>
          {#if c.tx_id}
            <span>→ {c.payee || c.description || ""} {c.posted ? fmtDate(c.posted) : ""} <span class="text-muted-foreground">{c.account_name || ""}</span>
              {#if c.applied}<Badge variant="secondary">{c.applied === "split" ? "split by items" : "categorized by items"}</Badge>{/if}</span>
            <span class="ml-auto flex gap-4">
              {#if c.amount < 0 && !c.applied && o.items.length}
                <Button variant="link" size="sm" class="h-auto p-0" title="Replace the category you set with the order's items"
                  onclick={() => undoablePost(charge(c.id, "apply"), charge(c.id, "restore"), undefined, () => "Split by items")}>Split by items</Button>
              {/if}
              <Button variant="link" size="sm" class="h-auto p-0" title="This charge isn't that transaction"
                onclick={() => undoablePost(charge(c.id, "unlink"), charge(c.id, "restore"), undefined, () => "Unmatched, and the transaction is back as it was")}>Not this transaction</Button>
            </span>
          {:else}
            <span class="text-muted-foreground">not matched to a transaction</span>
            <Button variant="link" size="sm" class="ml-auto h-auto p-0" disabled={!!picking[c.id]} onclick={() => pick(c.id)}>Pick one…</Button>
          {/if}
        </div>
        {#if picking[c.id]}
          {#await picking[c.id] then list}
            <div class="flex flex-col items-start gap-1 pl-4">
              {#each list as t (t.id)}
                <Button variant="link" size="sm" class="h-auto p-0" onclick={() => post(charge(c.id, "link"), { tx_id: t.id }, "Matched")}>
                  {fmtDate(t.posted)} · {t.payee || t.description} · {fmt(t.amount)} <span class="text-muted-foreground">{t.account_name}</span>
                </Button>
              {:else}
                <span class="text-xs text-muted-foreground">No transactions near that date and amount.</span>
              {/each}
            </div>
          {/await}
        {/if}
      {:else}
        <p class="text-xs text-muted-foreground">No card charges for this order yet.</p>
      {/each}
    </div>
  {/if}
</div>
