<script lang="ts">
  import { api } from "$lib/api";
  import CategorySelect from "$lib/components/CategorySelect.svelte";
  import { Button } from "$lib/components/ui/button";
  import { Badge } from "$lib/components/ui/badge";
  import { fmt, fmtDate } from "$lib/format";
  import { toast } from "svelte-sonner";
  import { ITEM_SOURCES, STORES, type RetailOrder } from "./retail";

  // An Amazon or Target order: its items, each with a category you can change (remembered for the next time you
  // buy it), and the card charges it was paid with. `onchange` runs after anything here changes a transaction.
  // Call loadCategories() before showing it.
  let { orderId, onchange }: { orderId: string; onchange?: () => void } = $props();

  let order = $state<Promise<RetailOrder>>(load());
  let picking = $state<Record<string, Promise<Candidate[]> | undefined>>({});
  type Candidate = { id: string; posted: string; amount: number; payee?: string; description?: string; account_name?: string };

  function load() { return api<RetailOrder>(`/api/retail/orders/${encodeURIComponent(orderId)}`, { keep: true }); }
  function changed() { onchange?.(); picking = {}; order = load(); }

  async function post(path: string, body: unknown, msg: string | ((r: any) => string)) {   // eslint-disable-line @typescript-eslint/no-explicit-any
    try {
      const r = await api(path, { method: "POST", body });
      toast.success(typeof msg === "string" ? msg : msg(r));
      changed();
    } catch (err) { toast.error((err as Error).message); }
  }
  const charge = (id: string, what: string) => `/api/retail/charges/${encodeURIComponent(id)}/${what}`;
  function pick(id: string) { picking[id] = api<Candidate[]>(charge(id, "candidates"), { keep: true }).catch(() => []); }
</script>

<div class="flex flex-col gap-3 rounded-lg bg-muted/40 p-4 text-sm">
  {#await order}
    <p class="text-muted-foreground">Loading…</p>
  {:then o}
    {@const totals = [o.subtotal != null ? `items ${fmt(o.subtotal)}` : "", o.shipping ? `shipping ${fmt(o.shipping)}` : "",
      o.tax != null ? `tax ${fmt(o.tax)}` : ""].filter(Boolean).join(" · ")}
    <div class="flex flex-wrap items-baseline gap-x-3 gap-y-1">
      <b>{STORES[o.retailer] || o.retailer} {o.channel === "store" ? "purchase" : "order"} {o.order_number}</b>
      <span class="text-xs text-muted-foreground tabular-nums">
        {o.placed ? fmtDate(o.placed, { month: "short", day: "numeric", year: "numeric" }) : ""}{o.total != null ? ` · ${fmt(o.total)}` : ""}{totals ? ` (${totals})` : ""}{o.payment ? ` · ${o.payment}` : ""}
      </span>
      <a class="ml-auto text-xs font-medium underline underline-offset-4" href={o.url} target="_blank" rel="noopener">
        Open on {o.retailer === "amazon" ? "amazon.com" : "target.com"}</a>
    </div>

    {#if o.items.length}
      <div class="flex flex-col">
        {#each o.items as i (i.id)}
          <div class="grid grid-cols-[1fr_auto] items-center gap-x-3 gap-y-1 border-t py-2 first:border-t-0 sm:grid-cols-[1fr_auto_14rem_9rem]">
            <span class="truncate" title={i.title}>{#if i.quantity > 1}<span class="text-muted-foreground">{i.quantity}×</span> {/if}{i.title}</span>
            <span class="text-right text-muted-foreground tabular-nums">{fmt(i.amount)}</span>
            <CategorySelect short ghost value={i.category ?? ""} label={`Category for ${i.title}`} class="w-full"
              onchange={(v) => v && post(`/api/retail/items/${i.id}`, { category: v }, (r) => (r.orders > 1 ? `Saved · used in ${r.orders} orders` : "Saved"))} />
            <span class="text-xs text-muted-foreground">{i.category ? ITEM_SOURCES[i.category_source ?? ""] ?? "" : "uses the transaction's category"}</span>
          </div>
        {/each}
      </div>
      <p class="text-xs text-muted-foreground">A category you pick here is used for this item in every order, now and next time.</p>
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
  {:catch err}
    <p class="text-muted-foreground">{err.message}</p>
  {/await}
</div>
