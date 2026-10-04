<script lang="ts">
  import { commas } from "$lib/commas";
  import { api } from "$lib/api";
  import { Button } from "$lib/components/ui/button";
  import { fmt, fmtDate, nb } from "$lib/format";
  import X from "@lucide/svelte/icons/x";
  import { onMount, tick } from "svelte";
  import type { Tracked } from "./types";
  import { errMsg } from "$lib/act";
  import { debounced } from "$lib/debounce";

  // Holdings you enter for an account that only reports a balance (a 401(k) through SimpleFIN, say). Every change
  // saves (after checking the rows add up); Done closes it and redraws the page if anything was saved.
  let { acctId, onclose }: { acctId: string; onclose: (changed: boolean) => void } = $props();

  type Row = { key: number; ticker: string; name: string; namePh: string; shares: string; value: string; pct: string };
  let seq = 0;
  const blank = (): Row => ({ key: seq++, ticker: "", name: "", namePh: "Fund name (if no ticker)", shares: "", value: "", pct: "" });
  let t = $state<Tracked | null>(null), err = $state("");
  let rows = $state<Row[]>([]);
  let status = $state(""), bad = $state(false);
  let changed = false;
  let box = $state<HTMLDivElement | null>(null);

  onMount(async () => {
    try { t = await api<Tracked>(`/api/tracked/${encodeURIComponent(acctId)}`); }
    catch (e) { err = errMsg(e); return; }
    rows = t.positions.length ? t.positions.map((p) => ({
      key: seq++, ticker: p.ticker ?? "", name: p.ticker ? "" : (p.name ?? ""), namePh: p.ticker ? (p.name ?? "") : "Fund name (if no ticker)",
      shares: p.ticker && p.shares != null ? String(+(+p.shares).toFixed(4)) : "",
      value: !p.ticker && p.last_value ? String(Math.round(p.last_value)) : "", pct: p.pct != null ? String(p.pct) : "",
    })) : [blank()];
    await tick();
    box?.querySelector<HTMLInputElement>(".tr-ticker")?.focus();
  });

  const say = (msg: string, isBad = false) => { status = msg; bad = isBad; };
  async function save() {
    const out = rows.map((r) => ({ ticker: r.ticker.trim(), name: r.name.trim(), shares: r.shares, value: r.value, pct: r.pct }))
      .filter((r) => r.ticker || r.name);
    if (!out.length) return say("");
    const total = out.reduce((a, r) => a + (Number(r.pct) || 0), 0);
    if (total > 0 && Math.abs(total - 100) > 0.5) return say(`Contribution percentages add up to ${total}%, not 100%`, true);
    if (out.some((r) => !r.ticker && !Number(r.value))) return say("A fund without a ticker needs its current value", true);
    if (out.some((r) => r.ticker && !Number(r.shares))) return say("Enter shares for each fund with a ticker", true);
    say("Saving…");
    try { await api(`/api/tracked/${encodeURIComponent(acctId)}`, { method: "POST", body: { rows: out } }); changed = true; say("Saved ✓ · prices updated"); }
    catch (e) { say(errMsg(e), true); }
  }
  const later = debounced(save, 150);
  const soon = () => later.call();
  async function add() {
    rows.push(blank());
    await tick();
    [...(box?.querySelectorAll<HTMLInputElement>(".tr-ticker") ?? [])].pop()?.focus();
  }
  const input = "h-8 rounded-md border border-input bg-transparent px-2 text-sm dark:bg-input/30";
</script>

<div bind:this={box} class="rounded-lg bg-muted/40 p-4" data-editor>
  <h3 class="font-semibold">What this account holds</h3>
  {#if err}<p class="mt-2 text-sm text-destructive">{err}</p>
  {:else if !t}<div class="mt-3 h-20 animate-pulse motion-reduce:animate-none rounded-md bg-muted"></div>
  {:else}
    <div class="mt-3 overflow-x-auto">
      <table class="text-sm">
        <thead><tr class="text-left text-xs text-muted-foreground [&>th]:pr-2 [&>th]:pb-1 [&>th]:font-medium">
          <th>Ticker</th><th>Name</th><th>Shares</th><th>Value (no ticker)</th><th>Contribution %</th><th></th>
        </tr></thead>
        <tbody>
          {#each rows as r, i (r.key)}
            <tr class="[&>td]:py-1 [&>td]:pr-2">
              <td><input class={`tr-ticker w-24 ${input}`} bind:value={r.ticker} onchange={soon} placeholder="FXAIX" aria-label="Ticker" /></td>
              <td><input class={`w-56 ${input}`} bind:value={r.name} onchange={soon} placeholder={r.namePh} aria-label="Fund name" /></td>
              <td><input class={`w-28 ${input}`} type="number" min="0" step="0.0001" value={r.shares} oninput={(e) => (r.shares = e.currentTarget.value)} onchange={soon} placeholder="shares" aria-label="Shares" /></td>
              <td><input class={`w-28 ${input}`} type="number" min="0" step="1" value={r.value} {@attach commas} oninput={(e) => (r.value = e.currentTarget.value)} onchange={soon} placeholder="or value $" aria-label="Value, for a fund without a ticker" /></td>
              <td><input class={`w-20 ${input}`} type="number" min="0" max="100" step="0.5" value={r.pct} oninput={(e) => (r.pct = e.currentTarget.value)} onchange={soon} placeholder="%" aria-label="Share of each contribution, %" /></td>
              <td><Button variant="ghost" size="icon" class="size-8" aria-label="Remove fund" onclick={() => { rows.splice(i, 1); save(); }}><X /></Button></td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
    <div class="mt-2 flex flex-wrap items-center gap-3">
      <Button variant="link" size="sm" class="px-0" onclick={add}>+ Add a fund</Button>
      <span class={bad ? "text-sm text-destructive" : "text-sm text-muted-foreground"} role="status">{status}</span>
      <Button variant="link" size="sm" class="ml-auto px-0" onclick={() => onclose(changed)}>Done</Button>
    </div>
    {#if t.contributions.length}
      <p class="mt-2 text-sm text-muted-foreground">Contributions spotted: {t.contributions.slice(0, 6).map((c) => nb(`${fmtDate(c.date)} ${fmt(c.amount)}`)).join(" · ")}</p>
    {/if}
  {/if}
</div>
