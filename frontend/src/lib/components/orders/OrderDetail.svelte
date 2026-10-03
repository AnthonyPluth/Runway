<script lang="ts">
  import { api } from "$lib/api";
  import { loadCategories } from "$lib/categories.svelte";
  import CategorySelect from "$lib/components/CategorySelect.svelte";
  import { Button } from "$lib/components/ui/button";
  import { Badge } from "$lib/components/ui/badge";
  import { fmt, fmtDate } from "$lib/format";
  import { toast } from "svelte-sonner";
  import { ITEM_SOURCES, STORES, STORE_SITES, type RetailOrder } from "./retail";

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
    catch (err) { failed = (err as Error).message; }
  }
  load();
  function changed() { onchange?.(); picking = {}; load(); }

  async function post(path: string, body: unknown, msg: string | ((r: any) => string)) {   // eslint-disable-line @typescript-eslint/no-explicit-any
    try {
      const r = await api(path, { method: "POST", body });
      toast.success(typeof msg === "string" ? msg : msg(r));
      changed();
    } catch (err) { toast.error((err as Error).message); }
  }
  // The AI's suggestions for items that have no category, from the button above the items: nothing is saved until you use
  // one (a suggested new category is created then).
  type Suggestion = { item_id: number; category: string | null; new_category: { name: string; parent: string | null } | null };
  let suggestions = $state<Record<number, Suggestion>>({});
  let asking = $state(false);
  async function suggest() {
    asking = true;
    try {
      const list = await api<Suggestion[]>(`/api/retail/orders/${encodeURIComponent(orderId)}/suggest`, { method: "POST" });
      suggestions = Object.fromEntries(list.map((s) => [s.item_id, s]));
      if (!list.length) toast("The AI had nothing to suggest");
    } catch (err) { toast.error((err as Error).message); }
    asking = false;
  }
  async function useSuggestion(s: Suggestion) {
    try {
      const r = await api<{ category: string; created: boolean; orders: number }>(`/api/retail/items/${s.item_id}`,
        { method: "POST", body: s.new_category ? { new_category: s.new_category } : { category: s.category } });
      if (r.created) await loadCategories();
      toast.success(r.created ? `Created ${r.category} and saved` : "Saved");
      delete suggestions[s.item_id];
      changed();
    } catch (err) { toast.error((err as Error).message); }
  }
  const charge = (id: string, what: string) => `/api/retail/charges/${encodeURIComponent(id)}/${what}`;
  function pick(id: string) { picking[id] = api<Candidate[]>(charge(id, "candidates"), { keep: true }).catch(() => []); }
</script>

<div class="flex flex-col gap-3 rounded-lg bg-muted/40 p-4 text-sm">
  {#if failed && !order}
    <p class="text-muted-foreground">{failed}</p>
  {:else if !order}
    <div class="h-16 animate-pulse motion-reduce:animate-none rounded-md bg-muted" aria-busy="true"></div>
  {:else}
    {@const o = order}
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
          <Button size="sm" variant="outline" disabled={asking} onclick={suggest}
            title="For the items with no category; it can propose a new one">{asking ? "Asking the AI…" : "Suggest categories with AI"}</Button>
        </div>
      {/if}
      {@const mine = family?.length ? o.items.filter((x) => x.category && family.includes(x.category)) : []}
      {@const items = mine.length && !showAll ? mine : o.items}
      <div class="flex flex-col" title="A category you pick here is used for this item in every order, now and next time">
        {#each items as i (i.id)}
          <div class="grid grid-cols-[1fr_auto] items-center gap-x-3 gap-y-1 border-t py-2 first:border-t-0 sm:grid-cols-[1fr_auto_14rem_9rem]">
            <span class="truncate" title={i.title}>{#if i.quantity > 1}<span class="text-muted-foreground">{i.quantity}×</span> {/if}{i.title}</span>
            <span class="text-right text-muted-foreground tabular-nums">{fmt(i.amount)}</span>
            <CategorySelect short ghost value={i.category ?? ""} label={`Category for ${i.title}`} class="w-full"
              onchange={(v) => v && post(`/api/retail/items/${i.id}`, { category: v }, (r) => (r.orders > 1 ? `Saved · used in ${r.orders} orders` : "Saved"))} />
            {#if !i.category && suggestions[i.id]}
              {@const s = suggestions[i.id]}
              <Button size="sm" variant="outline" class="h-auto justify-start py-1 whitespace-normal" aria-label={`${s.new_category ? `Create ${s.new_category.name} and use it` : `Use ${s.category}`} for ${i.title}`}
                onclick={() => useSuggestion(s)}>{s.new_category ? `New: ${s.new_category.name}` : `Use ${s.category}`}</Button>
            {:else}
              <span class="text-xs text-muted-foreground">{i.category ? ITEM_SOURCES[i.category_source ?? ""] ?? "" : "uses the transaction's category"}</span>
            {/if}
          </div>
        {/each}
      </div>
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
                  onclick={() => post(charge(c.id, "apply"), undefined, "Split by items")}>Split by items</Button>
              {/if}
              <Button variant="link" size="sm" class="h-auto p-0" title="This charge isn't that transaction"
                onclick={() => post(charge(c.id, "unlink"), undefined, "Unmatched, and the transaction is back as it was")}>Not this transaction</Button>
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
