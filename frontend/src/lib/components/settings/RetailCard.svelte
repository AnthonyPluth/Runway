<script lang="ts">
  import { api } from "$lib/api";
  import { app, refreshState } from "$lib/app.svelte";
  import ConfirmButton from "$lib/components/ConfirmButton.svelte";
  import OrderDetail from "$lib/components/orders/OrderDetail.svelte";
  import { orderLabel } from "$lib/components/orders/retail";
  import { Button } from "$lib/components/ui/button";
  import { fmt, fmtDate, relTime } from "$lib/format";
  import { cn } from "$lib/utils";
  import { toast } from "svelte-sonner";
  import ChevronRight from "@lucide/svelte/icons/chevron-right";
  import type { Snippet } from "svelte";
  import ServiceRow from "./ServiceRow.svelte";
  import type { RecentOrder, RetailStatus } from "./types";
  import { checkCls, helpCls, inputCls, linkCls, warnText } from "./ui";

  // The Runway browser extension: how to set it up (with the key it needs), what it has imported from each store
  // (`children` adds Carta's row to that list), and the recent orders, each opening to its items and charges. Its row
  // under Connections is On once it has a key, and needs attention when that key no longer works, or (`problem`) when
  // Carta's last read went wrong.
  let { children, problem = false }: { children?: Snippet; problem?: boolean } = $props();
  let data = $state<RetailStatus | null>(null);
  let error = $state("");
  let shownKey = $state("");
  let opened = $state(new Set<string>());
  let matching = $state(false);

  async function load() {
    try { data = await api<RetailStatus>("/api/retail"); error = ""; } catch (err) { error = (err as Error).message; }
  }
  load();
  // Anything you change here but the counts closes the open orders, as the classic card did when it redrew.
  async function again() { opened = new Set(); await load(); }
  // A match you make (or undo) in an open order: update the counts, leaving the order open.
  async function recount() { try { data = await api<RetailStatus>("/api/retail", { keep: true }); } catch { /* keep what's shown */ } }

  async function newKey() {
    try {
      const { token } = await api<{ token: string }>("/api/retail/token", { method: "POST" });
      await again();
      shownKey = token;
    } catch (err) { toast.error((err as Error).message); }
  }
  async function removeKey() {
    try { await api("/api/retail/token/remove", { method: "POST" }); toast.success("Key removed"); shownKey = ""; again(); }
    catch (err) { toast.error((err as Error).message); }
  }
  function copy(input: HTMLInputElement | null) {
    navigator.clipboard?.writeText(shownKey).then(() => toast.success("Copied"), () => {});
    input?.select();
  }
  async function setAi(e: Event) {
    try { await api("/api/retail/settings", { method: "POST", body: { ai: (e.currentTarget as HTMLInputElement).checked } }); toast.success("Saved"); }
    catch (err) { toast.error((err as Error).message); }
  }
  async function matchAgain() {
    matching = true;
    try {
      const out = await api<{ matched: number; split: number; category: number }>("/api/retail/match", { method: "POST" });
      toast.success(`${out.matched} newly matched · ${out.split} split · ${out.category} categorized`);
      refreshState(); await again();
    } catch (err) { toast.error((err as Error).message); }
    matching = false;
  }
  function toggle(id: string) {
    const next = new Set(opened);
    if (next.has(id)) next.delete(id); else next.add(id);
    opened = next;
  }
  const orderState = (o: RecentOrder) => !o.details ? "items not read" : !o.charges ? "no charge" : o.matched === o.charges ? "matched" : `${o.charges - (o.matched ?? 0)} not matched`;
  let keyInput = $state<HTMLInputElement | null>(null);
  const selectOnMount = (el: HTMLInputElement) => { el.select(); };
</script>

<ServiceRow name="Browser extension" purpose="Amazon, Target, Costco and Carta, read in your browser" on={!!data?.token}
  status={data?.token_problem ? "New key needed" : undefined} warn={!!data?.token_problem || problem}>
  {#if error}
    <p class="text-sm text-muted-foreground">{error}</p>
  {:else if !data}
    <p class="text-sm text-muted-foreground">Loading…</p>
  {:else}
    {@const r = data}
    {#if r.token}
      <div class={`${helpCls} flex flex-wrap items-center gap-x-2`}>
        <span>Key made {relTime(r.token_created)}{r.token_used ? `, last used ${relTime(r.token_used)}` : ", not used yet"}{r.token_expires ? `, works until ${fmtDate(r.token_expires)}` : ""}</span>
        {#if r.token_problem}
          <span class={warnText}>{r.token_problem === "expired" ? "This key has expired: make a new one." : "The person who made this key can no longer sign in: make a new one."}</span>
        {/if}
        <ConfirmButton class="h-auto px-1" confirm="Replace the key? The extension will need the new one" onconfirm={newKey}>Make a new key</ConfirmButton>
        <ConfirmButton class="h-auto px-1" confirm="Remove? The extension stops working" onconfirm={removeKey}>Remove</ConfirmButton>
      </div>
      {#if shownKey}
        <span class={`${helpCls} flex flex-wrap items-center gap-2`}>
          <input class={`${inputCls} w-full font-mono sm:w-96`} readonly value={shownKey} aria-label="Extension key" bind:this={keyInput} use:selectOnMount />
          <Button variant="outline" size="sm" onclick={() => copy(keyInput)}>Copy</Button>
          <span class="text-xs">Shown once: paste it into the extension's options now.</span>
        </span>
      {/if}
    {/if}
    <!-- Set once: open until there's a key, then folded away. -->
    <details class="group" open={!r.token}>
      <summary class="flex cursor-pointer list-none items-center gap-1.5 py-1 text-sm text-muted-foreground select-none [&::-webkit-details-marker]:hidden">
        <ChevronRight class="size-4 shrink-0 transition-transform group-open:rotate-90" aria-hidden="true" />Install steps</summary>
      <ol class={`${helpCls} mt-1 list-decimal space-y-1.5 pl-5`}>
        <li><a class={linkCls} href="/api/retail/extension.zip" download>Download the extension</a>, unzip it, and in Chrome (or Edge, Brave, Arc) open
          <code class="rounded bg-muted px-1 text-foreground">chrome://extensions</code>, turn on Developer mode and choose <b class="text-foreground">Load unpacked</b>.</li>
        <li>Give it Runway's address ({location.origin}) and a key{#if !r.token}: <Button variant="outline" size="sm" class="ml-1" onclick={newKey}>Make a key</Button>{/if}.</li>
        <li>Stay signed in to Amazon, Target, Costco and Carta in that browser, and use the extension's <b class="text-foreground">Import</b> button.</li>
      </ol>
    </details>

    <div class="flex flex-col">
      {#each (["amazon", "target", "costco"] as const) as k (k)}
        {@const s = r.stores[k]}
        <div class="flex min-h-10 flex-wrap items-center gap-x-3 gap-y-0.5 border-b py-1.5 last:border-b-0">
          <b class="text-sm">{s.name}</b>
          <span class="text-xs text-muted-foreground">
            {#if !s.last && !s.orders}not imported yet
            {:else}{s.orders} order{s.orders === 1 ? "" : "s"} · {s.matched} charge{s.matched === 1 ? "" : "s"} matched{#if s.unmatched}{" · "}<span class={warnText}
              title="Charges with no transaction: paid with a card that isn't in Runway, or not posted yet">{s.unmatched} not matched</span>{/if}{#if s.last}{` · imported ${relTime(s.last)}`}{/if}{/if}
          </span>
        </div>
      {/each}
      {@render children?.()}
    </div>

    <div class="flex flex-wrap items-center gap-3">
      {#if app.state?.has_api_key}
        <label class={checkCls}><input type="checkbox" checked={r.ai} onchange={setAi} /> Categorize items with AI</label>
      {/if}
      {#if r.recent.length}<Button variant="outline" size="sm" disabled={matching} onclick={matchAgain}>{matching ? "Working…" : "Match and split again"}</Button>{/if}
    </div>

    {#if r.recent.length}
      <details class="group">
        <summary class="flex cursor-pointer list-none items-center gap-1.5 py-1 text-sm text-muted-foreground select-none [&::-webkit-details-marker]:hidden">
          <ChevronRight class="size-4 shrink-0 transition-transform group-open:rotate-90" aria-hidden="true" />Recent orders</summary>
        <div class="mt-1 flex flex-col">
          {#each r.recent as o (o.id)}
            {@const st = orderState(o)}
            <div class="border-b last:border-b-0">
              <button type="button" class="grid w-full cursor-pointer grid-cols-[auto_1fr_auto] items-center gap-x-3 gap-y-0.5 rounded-md px-1 py-2 text-left text-sm hover:bg-muted/50 sm:grid-cols-[4.5rem_1fr_6rem_8rem_auto]"
                aria-expanded={opened.has(o.id)} onclick={() => toggle(o.id)}>
                <span class="text-xs text-muted-foreground">{o.placed ? fmtDate(o.placed) : ""}</span>
                <span class="min-w-0 truncate">{orderLabel(o)} <span class="text-xs text-muted-foreground">{o.order_number}</span></span>
                <span class="text-right tabular-nums">{o.total != null ? fmt(o.total) : ""}</span>
                <span class={cn("text-xs max-sm:col-start-2", o.charges && o.matched === o.charges ? "text-muted-foreground" : warnText)}>{st}</span>
                <ChevronRight class={cn("size-4 text-muted-foreground transition-transform max-sm:hidden", opened.has(o.id) && "rotate-90")} aria-hidden="true" />
              </button>
              {#if opened.has(o.id)}<div class="pb-3"><OrderDetail orderId={o.id} onchange={recount} /></div>{/if}
            </div>
          {/each}
        </div>
      </details>
    {/if}
  {/if}
</ServiceRow>
