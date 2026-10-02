<script lang="ts">
  import { api } from "$lib/api";
  import { autosave } from "$lib/autosave";
  import { Badge } from "$lib/components/ui/badge";
  import { Button } from "$lib/components/ui/button";
  import { barWidth, fmt } from "$lib/format";
  import { cn } from "$lib/utils";
  import Pencil from "@lucide/svelte/icons/pencil";
  import { tick } from "svelte";
  import LiveDot from "./LiveDot.svelte";
  import LogoPicker from "$lib/components/transactions/LogoPicker.svelte";
  import TickerIcon from "./TickerIcon.svelte";
  import { gainCls, pct, qty, signed } from "./numbers";
  import { inv, type SortKey } from "./state.svelte";
  import type { Holding } from "./types";

  // Every holding, sortable by any column, with the price paid per share editable in place.
  let { holdings, onchanged }: { holdings: Holding[]; onchanged: () => void } = $props();

  const keyOf = (h: Holding) => h.group || h.security_id;
  // While the cost basis editor is open the rows keep their order, so live prices don't move the row you're editing.
  let open = $state<string | null>(null);
  let frozen: string[] = [];
  const rows = $derived.by(() => {
    if (open && frozen.length) return frozen.map((k) => holdings.find((h) => keyOf(h) === k)).filter((h): h is Holding => !!h);
    const { key, dir } = inv.sort;
    return [...holdings].sort((a, b) => {
      const av = a[key] ?? -Infinity, bv = b[key] ?? -Infinity;
      return (typeof av === "string" ? av.localeCompare(String(bv)) : (av as number) - (bv as number)) * dir;
    });
  });
  function sortBy(k: SortKey) {
    inv.sort = { key: k, dir: inv.sort.key === k ? -inv.sort.dir : k === "name" ? 1 : -1 };
  }
  const liveAt = (t?: number) => (t ? new Date(t * 1000).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", second: "2-digit" }) : "");

  // Cost basis editor: one input per account holding the security (the institution's number is the default).
  let changed = false;
  let typed = $state<Record<number, string>>({});
  async function openEditor(h: Holding) {
    frozen = rows.map(keyOf);
    open = keyOf(h);
    changed = false;
    typed = Object.fromEntries(h.lots.map((l, i) => [i, l.manual && l.per_share != null ? String(+l.per_share.toFixed(4)) : ""]));
    await tick();
    document.querySelector<HTMLInputElement>("#inv-holdings .cb-in")?.focus();
  }
  function finish() {
    open = null; frozen = [];
    if (changed) onchanged();
  }
  const saveLot = (h: Holding, i: number) => async (f: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement) => {
    const l = h.lots[i];
    await api("/api/investments/cost", { method: "POST", body: { account_id: l.account_id, security_id: l.security_id || h.security_id, per_share: f.value } });
    changed = true;
    if (h.lots.length === 1) finish();   // one account: done as soon as it's saved
  };
  // Under 700px the secondary columns fold into the holding's cell, and the cost basis button goes with them (rendered once).
  const narrowQuery = typeof matchMedia === "function" ? matchMedia("(max-width: 700px)") : null;
  const narrow = $state({ on: narrowQuery?.matches ?? false });
  $effect(() => {
    if (!narrowQuery) return;
    const sync = () => { narrow.on = narrowQuery.matches; };
    narrowQuery.addEventListener("change", sync);
    sync();
    return () => narrowQuery.removeEventListener("change", sync);
  });
  const COLS: [SortKey, string, string][] = [
    ["name", "Holding", "text-left"], ["quantity", "Shares", "text-right max-[700px]:hidden"], ["price", "Price", "text-right max-[700px]:hidden"], ["value", "Value", "text-right"],
    ["day_change", "Today", "text-right max-[700px]:hidden"], ["gain", "Total gain", "text-right"], ["allocation", "Weight", "text-right max-[700px]:hidden"],
    ["cost_basis", "Cost basis", "text-right max-[700px]:hidden"],
  ];
</script>

{#snippet costBasis(x: Holding)}
            {#if x.is_cash || x.asset_class === "Not reported"}<span class="text-muted-foreground">—</span>
            {:else}
              <button class={cn("group inline-flex cursor-pointer items-center gap-1 rounded-md border border-dashed border-transparent px-1.5 py-0.5 hover:border-border focus-visible:border-border",
                x.gain == null ? "text-[var(--nw-1)]" : "text-muted-foreground hover:text-foreground")} title="Edit cost basis"
                aria-expanded={open === keyOf(x)} onclick={() => (open === keyOf(x) ? finish() : openEditor(x))}>
                {x.gain == null ? "Add" : fmt(x.cost_basis)}
                {#if x.cost_manual}<Badge variant="secondary" class="px-1.5 py-0 text-[11px]">edited</Badge>{/if}
                <Pencil class={cn("size-3", x.gain == null ? "opacity-70" : "opacity-0 group-hover:opacity-70 group-focus-visible:opacity-70")} aria-hidden="true" />
              </button>
            {/if}
{/snippet}

<div class="overflow-x-auto" id="inv-holdings">
  <table class="w-full text-sm">
    <thead>
      <tr class="text-xs text-muted-foreground">
        {#each COLS as [k, label, cls] (k)}
          <th class={cn("pb-2 font-medium [&:not(:first-child)]:pl-3", cls)} aria-sort={inv.sort.key === k ? (inv.sort.dir < 0 ? "descending" : "ascending") : undefined}>
            <button class="cursor-pointer whitespace-nowrap hover:text-foreground" onclick={() => sortBy(k)}>
              {label}{inv.sort.key === k ? (inv.sort.dir < 0 ? " ↓" : " ↑") : ""}
            </button>
          </th>
        {/each}
      </tr>
    </thead>
    <tbody>
      {#each rows as x (keyOf(x))}
        <tr class="border-t border-border align-top [&>td]:py-2 [&>td:not(:first-child)]:whitespace-nowrap [&>td:not(:first-child)]:pl-3">
          <td class="min-w-48 max-[700px]:min-w-0">
            <div class="flex gap-2.5">
              {#if x.is_cash}
                <TickerIcon ticker={x.ticker} name={x.name} logo={x.logo} />
              {:else}
                <!-- Click the logo to choose another (or none) for this holding. -->
                <LogoPicker name={x.name || x.ticker || "this holding"} holding={x.group || x.security_id} {onchanged}>
                  <TickerIcon ticker={x.ticker} name={x.name} logo={x.logo} />
                </LogoPicker>
              {/if}
              <div>
                <div><b>{x.ticker && !x.ticker.includes(":") ? x.ticker : ""}</b> {x.name ?? ""}</div>
                <div class="text-xs text-muted-foreground">{x.accounts.join(", ")}</div>
                {#if narrow.on}
                <div class="mt-0.5 text-xs text-muted-foreground tabular-nums">
                  {x.is_cash ? "Cash" : `${qty(x.quantity)} × ${fmt(x.price)}`}{x.day_change != null ? ` · today ${signed(x.day_change)}` : ""} · {(x.allocation * 100).toFixed(1)}%
                </div>
                <div>{@render costBasis(x)}</div>
                {/if}
              </div>
            </div>
          </td>
          <td class="text-right tabular-nums max-[700px]:hidden">{x.is_cash ? "—" : qty(x.quantity)}</td>
          <td class="text-right tabular-nums max-[700px]:hidden">
            {x.is_cash ? "—" : fmt(x.price)}
            {#if x.live}<LiveDot class="ml-1.5 size-[7px] align-[2px]" label="Live price" title={`Live price · ${liveAt(x.live_time)}`} />{/if}
          </td>
          <td class="text-right font-semibold tabular-nums">{fmt(x.value)}</td>
          <td class="text-right tabular-nums max-[700px]:hidden">
            {#if x.day_change == null}<span class="text-muted-foreground">—</span>
            {:else}<span class={gainCls(x.day_change)}>{signed(x.day_change)}</span><div class={cn("text-xs text-muted-foreground", gainCls(x.day_change_pct))}>{pct(x.day_change_pct, 2)}</div>{/if}
          </td>
          <td class="text-right tabular-nums">
            {#if x.gain == null}<span class="text-muted-foreground">—</span>
            {:else}<span class={gainCls(x.gain)}>{signed(x.gain)}</span><div class={cn("text-xs text-muted-foreground", gainCls(x.gain_pct))}>{pct(x.gain_pct)}</div>{/if}
          </td>
          <td class="text-right tabular-nums max-[700px]:hidden">
            <span class="mr-2 inline-block h-1.5 w-14 overflow-hidden rounded-full bg-muted align-middle"><span class="block h-full rounded-full bg-[var(--nw-1)]" style:width={barWidth(x.allocation)}></span></span><span class="inline-block w-12">{(x.allocation * 100).toFixed(1)}%</span>
          </td>
          <td class="text-right tabular-nums max-[700px]:hidden">
            {#if !narrow.on}{@render costBasis(x)}{/if}
          </td>
        </tr>
        {#if open === keyOf(x)}
          <tr class="bg-muted/40" data-editor>
            <td colspan="8" class="p-3">
              <p class="text-sm" title="Your average if you bought at different prices. Runway multiplies it by the shares you hold. Leave a box empty to go back to what the institution reports."><b>Price paid per share for {x.ticker || x.name || ""}</b></p>
              {#each x.lots as l, i (l.account_id)}
                <div class="mt-3 flex flex-wrap items-end gap-3">
                  <label class="flex flex-col gap-1 text-sm">{l.account_name} · {qty(Number(l.quantity))} shares
                    <span class="relative inline-block">
                      <span class="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true">$</span>
                      <input type="number" min="0" step="0.0001" class="cb-in h-9 w-40 rounded-md border border-input bg-transparent pr-2 pl-6 text-sm dark:bg-input/30"
                        value={typed[i]} oninput={(e) => (typed[i] = e.currentTarget.value)} use:autosave={saveLot(x, i)} aria-label={`Price per share in ${l.account_name}`}
                        placeholder={l.reported_cost_basis && l.quantity ? `reported ${(l.reported_cost_basis / l.quantity).toFixed(2)}` : "not reported"} />
                    </span>
                  </label>
                  <span class="pb-2 text-sm text-muted-foreground">{typed[i] && l.quantity ? `= ${fmt(Number(typed[i]) * l.quantity)} cost basis` : ""}</span>
                </div>
              {/each}
              <Button variant="link" size="sm" class="mt-2 px-0" onclick={finish}>Done</Button>
            </td>
          </tr>
        {/if}
      {/each}
    </tbody>
  </table>
</div>
