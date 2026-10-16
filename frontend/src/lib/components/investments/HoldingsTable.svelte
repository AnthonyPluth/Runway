<script lang="ts">
  import { api } from "$lib/api";
  import { autosave } from "$lib/autosave";
  import { Badge } from "$lib/components/ui/badge";
  import { Button } from "$lib/components/ui/button";
  import { barWidth, fmt, pct as share } from "$lib/format";
  import { cn } from "$lib/utils";
  import { commas } from "$lib/commas";
  import Pencil from "@lucide/svelte/icons/pencil";
  import { tick } from "svelte";
  import LiveDot from "./LiveDot.svelte";
  import LogoPicker from "$lib/components/transactions/LogoPicker.svelte";
  import TickerIcon from "./TickerIcon.svelte";
  import { gainCls, pct, qty, signed } from "./numbers";
  import { inv, type HoldView, type SortKey } from "./state.svelte";
  import type { Holding } from "./types";

  // Every holding: a table (sortable by any column) from 768px up, a list on a phone, with the price paid per share editable in place.
  let { holdings, onchanged }: { holdings: Holding[]; onchanged: () => void } = $props();

  function watch(query: string) {
    const mq = typeof matchMedia === "function" ? matchMedia(query) : null;
    const state = $state({ on: mq?.matches ?? false });
    $effect(() => {
      if (!mq) return;
      const sync = () => { state.on = mq.matches; };
      mq.addEventListener("change", sync);
      sync();
      return () => mq.removeEventListener("change", sync);
    });
    return state;
  }
  // Under 1280px (tablets and a narrow window next to the sidebar) the secondary columns fold into the holding's cell,
  // and the cost basis button goes with them (rendered once). Holding, Today, Total gain and Value stay as columns.
  const narrow = watch("(max-width: 1279px)");
  // The phone variant's own query (app.css): a phone shows the holdings as a list instead of a table.
  const phone = watch("(max-width: 767px), (max-height: 500px) and (pointer: coarse)");

  const keyOf = (h: Holding) => h.group || h.security_id;
  // While the cost basis editor is open the rows keep their order, so live prices don't move the row you're editing.
  let open = $state<string | null>(null);
  let frozen: string[] = [];

  // What the phone's switch can show, and the figure each sorts by when its active choice is tapped again.
  const VIEWS: [HoldView, string, "day_change_pct" | "gain_pct" | "allocation"][] = [["day", "Today", "day_change_pct"], ["total", "Total", "gain_pct"], ["weight", "Weight", "allocation"]];
  const figureKey = $derived(VIEWS.find(([v]) => v === inv.holdView)![2]);
  function pick(v: HoldView) {
    if (v !== inv.holdView) { inv.holdView = v; inv.listSort = { by: "value", dir: -1 }; return; }
    // The active choice again: sort by its figure, biggest first, then flip the direction on each further tap.
    inv.listSort = inv.listSort.by === "value" ? { by: "figure", dir: -1 } : { by: "figure", dir: inv.listSort.dir === -1 ? 1 : -1 };
  }

  const rows = $derived.by(() => {
    if (open && frozen.length) return frozen.map((k) => holdings.find((h) => keyOf(h) === k)).filter((h): h is Holding => !!h);
    if (phone.on) {
      const { by, dir } = inv.listSort, k = by === "value" ? "value" : figureKey;
      return [...holdings].sort((a, b) => {
        const av = a[k], bv = b[k];
        if (av == null || bv == null) return av == null ? (bv == null ? 0 : 1) : -1;   // no figure: last either way round
        return (av - bv) * dir;
      });
    }
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

  // The pill: mint for a gain, coral for a loss, muted for zero (or what rounds to it) and for nothing known; always signed.
  const TONE = { good: "bg-good/15 text-good", loss: "bg-loss/15 text-loss", muted: "bg-muted text-muted-foreground" };
  function tone(x: number | null | undefined, digits: number): keyof typeof TONE {
    if (x == null || !isFinite(x) || Number((Math.abs(x) * 100).toFixed(digits)) === 0) return "muted";
    return x > 0 ? "good" : "loss";
  }
  const hasCost = (h: Holding) => !h.is_cash && h.gain_pct != null;
  /** What the phone's pill says for a holding, and its tone. */
  function pillOf(h: Holding, view: HoldView): { text: string; tone: keyof typeof TONE } {
    if (view === "weight") return { text: share(h.allocation), tone: "muted" };
    if (view === "day") return { text: pct(h.day_change_pct, 2), tone: tone(h.day_change_pct, 2) };
    if (h.is_cash) return { text: "—", tone: "muted" };
    return hasCost(h) ? { text: pct(h.gain_pct), tone: tone(h.gain_pct, 1) } : { text: "No cost basis", tone: "muted" };
  }

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
  const toggleEditor = (h: Holding) => (open === keyOf(h) ? finish() : openEditor(h));
  const editable = (h: Holding) => !(h.is_cash || h.asset_class === "Not reported");
  const saveLot = (h: Holding, i: number) => async (f: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement) => {
    const l = h.lots[i];
    await api("/api/investments/cost", { method: "POST", body: { account_id: l.account_id, security_id: l.security_id || h.security_id, per_share: f.value } });
    changed = true;
    if (h.lots.length === 1) finish();   // one account: done as soon as it's saved
  };
  // Every width has the same order: Holding, Today, Total gain, Value, then the columns that only a wide screen has room for
  // (under 1280px they are folded into the holding's cell).
  const COLS: [SortKey, string, string][] = [
    ["name", "Holding", "text-left"],
    ["day_change", "Today", "text-right"], ["gain", "Total gain", "text-center"], ["value", "Value", "text-right"],
    ["quantity", "Shares", "text-right max-[1279px]:hidden"], ["price", "Price", "text-right max-[1279px]:hidden"],
    ["allocation", "Weight", "text-right max-[1279px]:hidden"], ["cost_basis", "Cost basis", "text-right max-[1279px]:hidden"],
  ];
</script>

{#snippet costBasis(x: Holding)}
            {#if !editable(x)}<span class="text-muted-foreground">—</span>
            {:else}
              <!-- Looks editable without hovering (a dotted underline, and the pencil always there on a touch screen) -->
              <button class={cn("group inline-flex cursor-pointer items-center gap-1 rounded-md border border-dashed border-transparent px-1.5 py-0.5 hover:border-border focus-visible:border-border max-md:min-h-10 whitespace-nowrap",
                x.gain == null ? "text-[var(--nw-1)]" : "text-muted-foreground hover:text-foreground")} title="Edit cost basis"
                aria-expanded={open === keyOf(x)} onclick={() => toggleEditor(x)}>
                <span class="underline decoration-dotted decoration-from-font underline-offset-4">{x.gain == null ? "Add" : fmt(x.cost_basis)}</span>
                {#if x.cost_manual}<Badge variant="secondary" class="px-1.5 py-0 text-[11px]">edited</Badge>{/if}
                <Pencil class={cn("size-3", x.gain == null ? "opacity-70" : "opacity-70 hoverable:opacity-0 hoverable:group-hover:opacity-70 hoverable:group-focus-visible:opacity-70")} aria-hidden="true" />
              </button>
            {/if}
{/snippet}

{#snippet pill(text: string, t: keyof typeof TONE, cls = "")}
  <span class={cn("inline-block rounded-full px-2 py-0.5 text-xs font-medium tabular-nums whitespace-nowrap", TONE[t], cls)}>{text}</span>
{/snippet}

{#snippet logo(x: Holding)}
  {#if x.is_cash}
    <TickerIcon ticker={x.ticker} name={x.name} logo={x.logo} />
  {:else}
    <!-- Click the logo to choose another (or none) for this holding. -->
    <LogoPicker name={x.name || x.ticker || "this holding"} holding={x.group || x.security_id} {onchanged}>
      <TickerIcon ticker={x.ticker} name={x.name} logo={x.logo} />
    </LogoPicker>
  {/if}
{/snippet}

{#snippet editor(x: Holding)}
              <p class="text-sm" title="Your average if you bought at different prices. Runway multiplies it by the shares you hold."><b>Price paid per share for {x.ticker || x.name || ""}</b></p>
              <p class="text-xs text-muted-foreground">Leave it empty to go back to the institution’s figure</p>
              {#each x.lots as l, i (l.account_id)}
                <div class="mt-3 flex flex-wrap items-end gap-3">
                  <label class="flex flex-col gap-1 text-sm">{l.account_name} · {qty(Number(l.quantity))} shares
                    <span class="relative inline-block">
                      <span class="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true">$</span>
                      <input type="number" min="0" step="0.0001" {@attach commas} class="cb-in h-9 w-40 rounded-md border border-input bg-transparent pr-2 pl-6 text-sm dark:bg-input/30"
                        value={typed[i]} oninput={(e) => (typed[i] = e.currentTarget.value)} use:autosave={saveLot(x, i)} aria-label={`Price per share in ${l.account_name}`}
                        placeholder={l.reported_cost_basis && l.quantity ? `reported ${(l.reported_cost_basis / l.quantity).toFixed(2)}` : "not reported"} />
                    </span>
                  </label>
                  <span class="pb-2 text-sm text-muted-foreground">{typed[i] && l.quantity ? `= ${fmt(Number(typed[i]) * l.quantity)} cost basis` : ""}</span>
                </div>
              {/each}
              <Button variant="link" size="sm" class="mt-2 px-0" onclick={finish}>Done</Button>
{/snippet}

{#snippet listBody(x: Holding)}
  {@const p = pillOf(x, inv.holdView)}
  <span class="min-w-0 flex-1">
    <span class="block truncate"><b>{x.ticker && !x.ticker.includes(":") ? x.ticker : (x.name ?? "")}</b></span>
    <span class="block truncate text-xs text-muted-foreground">{x.ticker && !x.ticker.includes(":") ? (x.name ?? "") : ""}{#if hasCost(x)}{x.ticker && !x.ticker.includes(":") && x.name ? " · " : ""}{pct(x.gain_pct)}{/if}</span>
  </span>
  <span class="flex shrink-0 flex-col items-end gap-1">
    <span class="text-sm font-semibold tabular-nums">{fmt(x.value)}</span>
    {@render pill(p.text, p.tone)}
  </span>
{/snippet}

{#if phone.on}
  <div id="inv-holdings">
    <div role="group" aria-label="Show" class="mb-2 flex gap-1 rounded-lg bg-muted p-[3px]">
      {#each VIEWS as [v, label] (v)}
        <button type="button" aria-pressed={inv.holdView === v} onclick={() => pick(v)}
          class="flex min-h-10 flex-1 cursor-pointer items-center justify-center gap-1 rounded-md border border-transparent px-2 text-sm font-medium text-foreground/70 aria-pressed:bg-input/30 aria-pressed:text-foreground aria-pressed:shadow-sm focus-visible:ring-[3px] focus-visible:ring-ring/50">
          {label}{inv.holdView === v && inv.listSort.by === "figure" ? (inv.listSort.dir < 0 ? " ↓" : " ↑") : ""}
        </button>
      {/each}
    </div>
    <ul>
      {#each rows as x (keyOf(x))}
        <li class="border-t border-border first:border-t-0" data-holding={keyOf(x)}>
          <div class="flex items-center gap-3 py-2.5">
            <div class="flex size-10 shrink-0 items-center justify-center rounded-xl bg-muted [&_img]:mt-0 [&_span]:mt-0">{@render logo(x)}</div>
            {#if editable(x)}
              <button type="button" class="flex min-h-10 min-w-0 flex-1 cursor-pointer items-center gap-3 text-left" aria-expanded={open === keyOf(x)} title="Edit cost basis" onclick={() => toggleEditor(x)}>
                {@render listBody(x)}
              </button>
            {:else}
              <div class="flex min-w-0 flex-1 items-center gap-3">{@render listBody(x)}</div>
            {/if}
          </div>
          {#if open === keyOf(x)}
            <div class="mb-2 rounded-lg bg-muted/40 p-3" data-editor>{@render editor(x)}</div>
          {/if}
        </li>
      {/each}
    </ul>
  </div>
{:else}
<div class="overflow-x-auto" id="inv-holdings">
  <table class="w-full text-sm">
    <thead>
      <tr class="text-xs text-muted-foreground">
        {#each COLS as [k, label, cls] (k)}
          <th data-col={k} class={cn("pb-2 font-medium [&:not(:first-child)]:pl-2 min-[1280px]:[&:not(:first-child)]:pl-3", cls)} aria-sort={inv.sort.key === k ? (inv.sort.dir < 0 ? "descending" : "ascending") : undefined}>
            <button class="cursor-pointer whitespace-nowrap hover:text-foreground" onclick={() => sortBy(k)}>
              {label}{inv.sort.key === k ? (inv.sort.dir < 0 ? " ↓" : " ↑") : ""}
            </button>
          </th>
        {/each}
      </tr>
    </thead>
    <tbody>
      {#each rows as x (keyOf(x))}
        <tr class="border-t border-border align-top [&>td]:py-2 [&>td:not(:first-child)]:whitespace-nowrap [&>td:not(:first-child)]:pl-2 min-[1280px]:[&>td:not(:first-child)]:pl-3">
          <td class="min-w-48 max-[1279px]:min-w-0">
            <div class="flex gap-2.5">
              {@render logo(x)}
              <div class="min-w-0">
                <div><b>{x.ticker && !x.ticker.includes(":") ? x.ticker : ""}</b> {x.name ?? ""}</div>
                <div class="text-xs text-muted-foreground">{x.accounts.join(", ")}</div>
                {#if narrow.on}
                <div class="mt-0.5 text-xs text-muted-foreground tabular-nums">
                  {x.is_cash ? "Cash" : `${qty(x.quantity)} × ${fmt(x.price)}`}{" "}· {share(x.allocation)}
                </div>
                <div>{@render costBasis(x)}</div>
                {/if}
              </div>
            </div>
          </td>
          <td class="text-right tabular-nums whitespace-nowrap">
            {#if x.day_change == null}<span class="text-muted-foreground">—</span>
            {:else}<span class={gainCls(x.day_change)}>{signed(x.day_change)}</span>{#if x.day_change_pct != null}<div class="mt-0.5">{@render pill(pct(x.day_change_pct, 2), tone(x.day_change_pct, 2))}</div>{/if}{/if}
          </td>
          <td class="text-center tabular-nums">
            {#if x.gain == null}<span class="text-muted-foreground">—</span>
            {:else}<span class={gainCls(x.gain)}>{signed(x.gain)}</span><div class="mt-0.5">{@render pill(pct(x.gain_pct), tone(x.gain_pct, 1))}</div>{/if}
          </td>
          <td class="text-right tabular-nums" data-col="value">
            <span class="font-semibold">{fmt(x.value)}</span>
          </td>
          <td class="text-right tabular-nums max-[1279px]:hidden">{x.is_cash ? "—" : qty(x.quantity)}</td>
          <td class="text-right tabular-nums max-[1279px]:hidden">
            {x.is_cash ? "—" : fmt(x.price)}
            {#if x.live}<LiveDot class="ml-1.5 size-[7px] align-[2px]" label="Live price" title={`Live price · ${liveAt(x.live_time)}`} />{/if}
          </td>
          <td class="text-right tabular-nums max-[1279px]:hidden">
            <span class="mr-2 inline-block h-1.5 w-14 overflow-hidden rounded-full bg-muted align-middle"><span class="block h-full rounded-full bg-[var(--nw-1)]" style:width={barWidth(x.allocation)}></span></span><span class="inline-block w-12">{share(x.allocation)}</span>
          </td>
          <td class="text-right tabular-nums max-[1279px]:hidden">
            {#if !narrow.on}{@render costBasis(x)}{/if}
          </td>
        </tr>
        {#if open === keyOf(x)}
          <tr class="bg-muted/40" data-editor>
            <td colspan="8" class="p-3">{@render editor(x)}</td>
          </tr>
        {/if}
      {/each}
    </tbody>
  </table>
</div>
{/if}
