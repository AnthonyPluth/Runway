<script lang="ts">
  import { api } from "$lib/api";
  import { autosave } from "$lib/autosave";
  import { Button } from "$lib/components/ui/button";
  import { ConfirmDialog } from "$lib/components/ui/confirm-dialog";
  import { fmt, fmt0, fmtDate, parseDate } from "$lib/format";
  import { commas } from "$lib/commas";
  import ExternalLink from "@lucide/svelte/icons/external-link";
  import { toast } from "svelte-sonner";
  import { HOME_VALUES, valueSource } from "./homeValues";
  import type { Asset, NetWorth } from "./types";

  // A home, vehicle or other asset as the side panel shows it: its value as the one field to change it (a home Realie values shows the figure only),
  // where the value came from, the value link, and removing it. `onremoved` runs after Remove goes through (the panel closes).
  let { a, d, onedit, onchanged, onremoved }: { a: Asset; d: NetWorth; onedit: () => void; onchanged: () => void; onremoved: () => void } = $props();

  // Where to check what it's worth: your own link, Zillow for a home with an address, KBB for a vehicle.
  const link = $derived(a.url && /^https?:\/\//i.test(a.url) ? { href: a.url, label: a.url.includes("zillow") ? "Zillow" : a.url.includes("kbb") ? "KBB" : "Link" }
    : a.kind === "home" && a.address ? { href: `https://www.zillow.com/homes/${encodeURIComponent(a.address)}_rb/`, label: "Zillow" }
    : a.kind === "vehicle" ? { href: "https://www.kbb.com/whats-my-car-worth/", label: "KBB" } : null);
  const loan = $derived(d.loan_accounts.find((l) => l.id === a.loan_account_id));
  const item = $derived(d.groups.flatMap((g) => g.items).find((i) => i.type === "asset" && i.id === a.id));
  const stale = $derived(!!a.as_of && (Date.now() - parseDate(a.as_of).getTime()) / 864e5 > (a.kind === "home" ? 120 : 90));
  const canRefresh = $derived(a.kind === "home" && d.realie.configured && !!a.address);

  async function saveValue(f: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement) {
    await api(`/api/assets/${a.id}`, { method: "POST", body: { value: f.value } });
    toast("Value updated");
    onchanged();
  }
  let looking = $state(false);
  async function refresh() {
    looking = true;
    try {
      const r = await api<{ value: number; low: number | null; high: number | null }>(`/api/assets/${a.id}/refresh`, { method: "POST" });
      toast(HOME_VALUES.refreshed(r));
      onchanged();
    } catch (err) { toast.error((err as Error).message); looking = false; }
  }
  let removing = $state(false);
  async function remove(): Promise<boolean> {
    try { await api(`/api/assets/${a.id}/remove`, { method: "POST" }); toast("Removed"); onremoved(); onchanged(); return true; }
    catch (err) { toast.error((err as Error).message); return false; }
  }
</script>

<div>
  <div>
    <div class="min-w-0">
      <!-- A home Realie values isn't valued by hand too (the server refuses it): its next lookup would only undo it -->
      {#if a.realie_valued}
        <div class="text-2xl font-semibold tabular-nums">{fmt(a.current_value)}</div>
      {:else}
        <label class="flex flex-col gap-1 text-sm">Value
          <span class="relative">
            <span class="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true">$</span>
            <input type="number" min="0" step="100" value={Math.round(a.current_value)} {@attach commas} use:autosave={saveValue}
              class="h-9 w-44 rounded-md border border-input bg-transparent pr-2 pl-6 text-sm dark:bg-input/30" />
          </span>
        </label>
      {/if}
      <div class="mt-1 text-sm text-muted-foreground">
        {valueSource(a.source)}{a.source !== "manual" && a.low && a.high ? ` (range ${fmt0(a.low)}–${fmt0(a.high)})` : ""}
        · {a.as_of ? fmtDate(a.as_of, { month: "short", day: "numeric", year: "numeric" }) : "—"}{a.yearly_change ? ` · ${a.yearly_change > 0 ? "+" : "−"}${Math.abs(a.yearly_change)}% a year since` : ""}
        {#if stale} · <span class="font-semibold text-[var(--warning)]">worth a fresh look</span>{/if}
      </div>
      {#if a.address}<div class="text-sm text-muted-foreground">{a.address}</div>{/if}
      {#if loan}<div class="text-sm">{fmt(item?.equity ?? 0)} equity after {loan.name} ({fmt0(item?.loan?.owed ?? 0)} owed)</div>{/if}
    </div>
  </div>
  <div class="mt-3 flex flex-wrap items-center gap-2">
    {#if canRefresh}
      {#if a.next_lookup}<span class="text-sm text-muted-foreground">{HOME_VALUES.nextLookup(a.next_lookup)}</span>
      {:else}<Button variant="outline" size="sm" disabled={looking} onclick={refresh}>{looking ? "Looking up…" : HOME_VALUES.refresh}</Button>{/if}
      <span class="text-xs text-muted-foreground">· {HOME_VALUES.lookups(d.realie.used, d.realie.limit)}</span>
    {/if}
    {#if link}
      <Button variant="link" size="sm" href={link.href} target="_blank" rel="noopener">Check on {link.label} <ExternalLink class="size-3.5" /></Button>
    {/if}
    <Button variant="link" size="sm" onclick={onedit}>Edit details</Button>
    <Button variant="link" size="sm" onclick={() => (removing = true)}>Remove</Button>
  </div>
</div>

<ConfirmDialog bind:open={removing} destructive title={`Remove ${a.name}?`} description="Its value history goes with it, and it stops counting toward your net worth."
  confirmLabel="Remove" busyLabel="Removing…" onconfirm={remove} />
