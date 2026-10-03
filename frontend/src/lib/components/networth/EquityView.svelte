<script lang="ts">
  import { api } from "$lib/api";
  import { autosave } from "$lib/autosave";
  import ConfirmButton from "$lib/components/ConfirmButton.svelte";
  import EmptyLine from "$lib/components/EmptyLine.svelte";
  import LineChart from "$lib/components/investments/LineChart.svelte";
  import StatStrip from "$lib/components/StatStrip.svelte";
  import * as Alert from "$lib/components/ui/alert";
  import { Badge } from "$lib/components/ui/badge";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { Input } from "$lib/components/ui/input";
  import { barWidth, fmt, fmt0, fmtDate, isoDay, pct, shortMoney } from "$lib/format";
  import { commas } from "$lib/commas";
  import { viewport } from "$lib/phone.svelte";
  import TriangleAlert from "@lucide/svelte/icons/triangle-alert";
  import { toast } from "svelte-sonner";
  import { EQ_KINDS, isOption, shares, stalePrice, todayIndex, vestingSeries } from "./equity";
  import GrantSheet from "./GrantSheet.svelte";
  import RefreshFailed from "./RefreshFailed.svelte";
  import type { Company, Equity, Grant, GrantBody } from "./types";

  // #networth/equity: stock options, RSUs and shares, what's vested and what it's worth, by company; from Carta or entered
  // by hand. Net worth counts vested equity, and its summary loads afresh when you go back to it. A reload that fails
  // keeps the figures on screen under a "Couldn't refresh" line.
  let d = $state<Equity | null>(null), error = $state("");
  async function load() {
    try { d = await api<Equity>("/api/equity"); error = ""; } catch (err) { error = (err as Error).message; }
  }
  load();

  const c = $derived(d?.carta);
  const lastRead = $derived([c?.web_last, c?.last_sync].filter(Boolean).sort().at(-1));
  const chart = $derived(d?.companies.length ? vestingSeries(d.companies) : null);
  const todayAt = $derived(chart ? todayIndex(chart.xs, isoDay()) : null);
  // A color for each company's line, and the muted one for "Other" (the smaller companies added up).
  const CLS = ["s-main", "s-alt", "s-4", "s-5"] as const;
  const clsOf = (name: string, i: number) => (name === "Other" && i === (chart?.lines.length ?? 0) - 1 && i >= CLS.length - 1 ? "s-muted" : CLS[i % CLS.length]);
  const my = (s: string | null | undefined, o: Intl.DateTimeFormatOptions = { month: "short", year: "numeric" }) => (s ? fmtDate(s, o) : "");

  async function post(url: string, body: unknown, msg: string): Promise<boolean> {
    try { await api(url, { method: "POST", body }); if (msg) toast(msg); await load(); return true; }
    catch (err) { toast.error((err as Error).message); return false; }
  }
  // Adding a company: a form (Enter adds it), the name required, Add waiting for the save.
  let adding = $state(false), coName = $state(""), coPrice = $state(""), coBusy = $state(false), coErr = $state<string | null>(null);
  const startAdding = () => { adding = true; coName = ""; coPrice = ""; coErr = null; };
  async function addCompany(e: SubmitEvent) {
    e.preventDefault();
    if (coBusy) return;
    if (!coName.trim()) { coErr = "Give the company a name"; (e.currentTarget as HTMLFormElement).querySelector<HTMLInputElement>("input")?.focus(); return; }
    coBusy = true; coErr = null;
    try { await api("/api/equity/companies", { method: "POST", body: { name: coName, share_price: coPrice } }); toast("Company added"); adding = false; await load(); }
    catch (err) { coErr = (err as Error).message; }
    finally { coBusy = false; }
  }
  // Carta's last errors, as a plain first line and the reason (Settings → Connections is where it's fixed).
  const cartaProblems = $derived([
    c?.last_error ? { title: "Couldn’t sync from Carta", detail: c.last_error } : null,
    c?.web_error ? { title: "Couldn’t read Carta through the browser extension", detail: c.web_error } : null,
  ].filter((x): x is { title: string; detail: string } => !!x));
  const today = isoDay();
  let syncing = $state(false);
  async function sync() {
    syncing = true;
    try {
      const r = await api<{ companies: number; grants: number }>("/api/carta/sync", { method: "POST" });
      toast(`Carta: ${r.companies} compan${r.companies === 1 ? "y" : "ies"}, ${r.grants} grant${r.grants === 1 ? "" : "s"}`);
    } catch (err) { toast.error((err as Error).message); }
    syncing = false;
    await load();
  }
  // The grant side panel: adding to a company, or editing one of its grants.
  let sheetOpen = $state(false), sheetCo = $state<Company | null>(null), sheetGrant = $state<Grant | null>(null);
  function openSheet(co: Company, g: Grant | null) { sheetCo = co; sheetGrant = g; sheetOpen = true; }
  // Saving a grant: the panel closes once it's saved; a refusal stays in the panel, under the form.
  async function saveGrant(body: GrantBody): Promise<string | null> {
    if (!sheetCo) return null;
    try {
      if (sheetGrant) await api(`/api/equity/grants/${encodeURIComponent(sheetGrant.id)}`, { method: "POST", body });
      else await api(`/api/equity/companies/${cid(sheetCo)}/grants`, { method: "POST", body });
    } catch (err) { return (err as Error).message; }
    toast(sheetGrant ? "Grant saved" : "Grant added");
    sheetOpen = false;
    await load();
    return null;
  }
  const cid = (co: Company) => encodeURIComponent(co.id);
  const stats = $derived(d ? [
    { label: "Vested now", value: fmt0(d.vested_value), sub: d.in_networth !== d.vested_value ? `${fmt0(d.in_networth)} counted in net worth` : "counted in net worth" },
    { label: "Still to vest", value: fmt0(d.unvested_value), sub: "at today’s share prices" },
  ] : []);
</script>

<div class="mb-6 flex flex-wrap items-center justify-end gap-2">
  {#if c?.connected}<Button variant="outline" size="sm" disabled={syncing} onclick={sync}>{syncing ? "Reading Carta…" : "Sync from Carta"}</Button>{/if}
  <Button size="sm" variant="outline" onclick={startAdding}>Add a company</Button>
</div>

{#if error && !d}
  <Card.Root>
    <Card.Content>
      <p class="text-sm">Something went wrong: {error}</p>
      <Button class="mt-3" variant="outline" onclick={load}>Try again</Button>
    </Card.Content>
  </Card.Root>
{:else if !d || !c}<div class="h-40 animate-pulse motion-reduce:animate-none rounded-xl bg-muted"></div>
{:else}
  {#if error}<RefreshFailed {error} onretry={load} class="mb-6" />{/if}
  {#if lastRead}<p class="mb-6 text-sm text-muted-foreground">From Carta, last read {fmtDate(lastRead)}.</p>{/if}
  {#each cartaProblems as p (p.title)}
    <Alert.Root variant="destructive" class="mb-6" data-testid="carta-problem"><TriangleAlert /><Alert.Description>
      <p><span class="font-medium text-foreground">{p.title}</span> · <a class="font-medium underline underline-offset-4" href="#setup/connections">Fix in Settings</a></p>
      <p class="text-xs">{p.detail}</p>
    </Alert.Description></Alert.Root>
  {/each}

  {#if d.companies.length}
    <StatStrip items={stats} />
    <p class="mt-2 mb-6 text-sm text-muted-foreground">Vested value at the last price you entered, before tax</p>
    {#if chart}
      <Card.Root class="mb-6">
        <Card.Header><Card.Title>Vesting over time</Card.Title></Card.Header>
        <Card.Content>
          <LineChart xs={chart.xs.map((x) => fmtDate(x, { month: "short", year: "numeric" }))} labels zero height={200} mark={todayAt == null ? null : { at: todayAt, label: "Today" }} fmtY={shortMoney} fmtTip={fmt}
            series={chart.lines.map((l, i) => ({ ...l, cls: clsOf(l.name, i), step: true }))} />
        </Card.Content>
      </Card.Root>
    {/if}
  {/if}

  {#if adding}
    <form class="mb-6 rounded-lg bg-muted/40 p-4" data-editor novalidate onsubmit={addCompany}>
      <div class="flex flex-wrap items-end gap-3">
        <label class="flex flex-col gap-1 text-sm">Company<Input class="w-56" bind:value={coName} placeholder="Acme, Inc." autofocus required
          aria-invalid={!!coErr && !coName.trim()} oninput={() => (coErr = null)} /></label>
        <label class="flex flex-col gap-1 text-sm">Share price
          <span class="relative">
            <span class="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true">$</span>
            <Input class="w-32 pl-6" type="number" min="0" step="0.01" value={coPrice} {@attach commas} oninput={(e) => (coPrice = e.currentTarget.value)} placeholder="0.00" />
          </span>
        </label>
        <Button type="submit" size="sm" disabled={coBusy}>{coBusy ? "Adding…" : "Add"}</Button>
        <Button type="button" variant="link" size="sm" onclick={() => (adding = false)}>Cancel</Button>
      </div>
      {#if coErr}<p class="mt-2 text-sm text-destructive" role="alert">{coErr}</p>{/if}
    </form>
  {/if}

  {#if !d.companies.length && !adding}
    <EmptyLine label="Equity" message="none yet" action="Add a company" onaction={startAdding} />
  {/if}

  {#each d.companies as co (co.id)}
    {@const old = stalePrice(co.price_as_of, today)}
    <Card.Root class="mb-6">
      <Card.Content>
        <div class="mb-2 flex flex-wrap items-center gap-3">
          <b>{co.name}</b>{#if co.source === "carta"}<Badge variant="secondary">Carta</Badge>{/if}
          <label class="inline-flex items-center gap-2 text-sm">Share price
            <span class="relative">
              <span class="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true">$</span>
              <input type="number" min="0" step="0.01" value={co.share_price ?? ""} {@attach commas} placeholder="0.00"
                class="h-8 w-28 rounded-md border border-input bg-transparent pr-2 pl-6 text-sm dark:bg-input/30"
                use:autosave={(f) => post(`/api/equity/companies/${cid(co)}`, { share_price: f.value }, "Share price saved")} />
            </span>
          </label>
          <span class="text-sm text-muted-foreground">{co.price_as_of ? `as of ${fmtDate(co.price_as_of, { month: "short", day: "numeric", year: "numeric" })}` : "no price yet"}{#if old}{" · "}<span class="font-medium text-warning" data-testid="stale-price">{old}</span>{/if}</span>
          <label class="inline-flex items-center gap-2 text-sm">
            <input type="checkbox" class="size-4" checked={!!co.in_networth}
              onchange={(e) => post(`/api/equity/companies/${cid(co)}`, { in_networth: e.currentTarget.checked }, "Saved")} /> Count in net worth
          </label>
          <span class="ml-auto inline-flex gap-1">
            <Button variant="link" size="sm" onclick={() => openSheet(co, null)}>Add a grant</Button>
            <ConfirmButton confirm="Remove it and its grants?" onconfirm={() => void post(`/api/equity/companies/${cid(co)}/remove`, {}, "Removed")}>Remove</ConfirmButton>
          </span>
        </div>
        {#if co.grants.length && viewport.phone}
          <!-- On a phone, a card per grant with every figure the table has, and actions big enough to tap -->
          <ul class="divide-y divide-border" data-testid="grant-cards">
            {#each co.grants as g (g.id)}
              {@const p = g.quantity ? g.vested / g.quantity : 0}
              <li class="py-3">
                <div class="flex items-baseline justify-between gap-3">
                  <span class="min-w-0 font-medium">{g.label || EQ_KINDS[g.kind]}</span>
                  <span class="font-semibold tabular-nums">{fmt(g.vested_value)}</span>
                </div>
                <div class="text-xs text-muted-foreground">{[g.label ? EQ_KINDS[g.kind] : "", g.expires_on && isOption(g.kind) ? `expires ${my(g.expires_on)}` : ""].filter(Boolean).join(" · ")}</div>
                {@render vesting(g, p)}
                <dl class="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
                  <dt class="text-muted-foreground">Granted</dt><dd class="text-right">{g.granted_on ? my(g.granted_on) : "—"}</dd>
                  <dt class="text-muted-foreground">Shares</dt><dd class="text-right tabular-nums">{shares(g.quantity)}</dd>
                  {#if isOption(g.kind)}<dt class="text-muted-foreground">Strike</dt><dd class="text-right tabular-nums">{fmt(g.strike)}</dd>{/if}
                  <dt class="text-muted-foreground">Still to vest</dt><dd class="text-right tabular-nums">{fmt(g.unvested_value)}</dd>
                </dl>
                {@render actions(co, g, "min-h-11 px-3")}
              </li>
            {/each}
          </ul>
        {:else if co.grants.length}
          <div class="overflow-x-auto">
            <table class="w-full text-sm">
              <thead><tr class="text-xs text-muted-foreground [&>th]:pb-1 [&>th]:font-medium [&>th:not(:first-child)]:pl-3">
                <th class="text-left">Grant</th><th class="text-left max-[700px]:hidden">Granted</th><th class="text-right">Shares</th>
                <th class="text-right max-[700px]:hidden">Strike</th><th class="text-left">Vested</th><th class="text-right">Worth now</th>
                <th class="text-right max-[700px]:hidden">Still to vest</th><th></th>
              </tr></thead>
              <tbody>
                {#each co.grants as g (g.id)}
                  {@const p = g.quantity ? g.vested / g.quantity : 0}
                  <tr class="border-t border-border align-top [&>td]:py-2 [&>td:not(:first-child)]:pl-3">
                    <td>{g.label || EQ_KINDS[g.kind]}
                      <div class="text-xs text-muted-foreground">{[g.label ? EQ_KINDS[g.kind] : "", g.expires_on && isOption(g.kind) ? `expires ${my(g.expires_on)}` : ""].filter(Boolean).join(" · ")}</div></td>
                    <td class="text-muted-foreground max-[700px]:hidden">{g.granted_on ? my(g.granted_on) : "—"}</td>
                    <td class="text-right tabular-nums">{shares(g.quantity)}</td>
                    <td class="text-right text-muted-foreground tabular-nums max-[700px]:hidden">{isOption(g.kind) ? fmt(g.strike) : "—"}</td>
                    <td class="min-w-32">{@render vesting(g, p)}</td>
                    <td class="text-right tabular-nums">{fmt(g.vested_value)}</td>
                    <td class="text-right text-muted-foreground tabular-nums max-[700px]:hidden">{fmt(g.unvested_value)}</td>
                    <td class="text-right whitespace-nowrap">{@render actions(co, g, "px-1")}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
        {/if}
      </Card.Content>
    </Card.Root>
  {/each}
{/if}

{#snippet vesting(g: Grant, p: number)}
  <div class="flex items-center gap-2">
    <span class="h-1.5 flex-1 overflow-hidden rounded-full bg-muted"><i class="block h-full rounded-full bg-[var(--nw-1)]" style:width={barWidth(p)}></i></span>
    <span class="w-9 text-right text-xs tabular-nums">{pct(p)}</span>
  </div>
  <div class="text-xs text-muted-foreground">{shares(g.vested)} of {shares(g.quantity)}{g.fully_vested_on && p < 1 ? ` · all by ${my(g.fully_vested_on)}` : ""}{g.exercised ? ` · ${shares(g.exercised)} exercised` : ""}</div>
  {#if g.problem}<div class="text-xs text-(--low)">{g.problem}</div>{/if}
{/snippet}

{#snippet actions(co: Company, g: Grant, cls: string)}
  <!-- relative: ConfirmButton's screen-reader line stays inside the table's scroll box rather than widening the page -->
  <span class="relative inline-flex gap-1">
    <Button variant="link" size="sm" class={cls} aria-label={`Edit ${g.label || EQ_KINDS[g.kind]}`} onclick={() => openSheet(co, g)}>Edit</Button>
    <ConfirmButton class={cls} confirm="Remove?" onconfirm={() => void post(`/api/equity/grants/${encodeURIComponent(g.id)}/remove`, {}, "Removed")}>Remove</ConfirmButton>
  </span>
{/snippet}

<GrantSheet bind:open={sheetOpen} g={sheetGrant} company={sheetCo?.name ?? ""} onsave={saveGrant} />
