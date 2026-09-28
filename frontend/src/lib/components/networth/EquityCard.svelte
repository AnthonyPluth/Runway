<script lang="ts">
  import { api } from "$lib/api";
  import { autosave } from "$lib/autosave";
  import ConfirmButton from "$lib/components/ConfirmButton.svelte";
  import LineChart from "$lib/components/investments/LineChart.svelte";
  import * as Alert from "$lib/components/ui/alert";
  import { Badge } from "$lib/components/ui/badge";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { Input } from "$lib/components/ui/input";
  import { fmt, fmt0, fmtDate, shortMoney } from "$lib/format";
  import TriangleAlert from "@lucide/svelte/icons/triangle-alert";
  import { toast } from "svelte-sonner";
  import { EQ_KINDS, isOption, shares, vestingSeries } from "./equity";
  import GrantForm from "./GrantForm.svelte";
  import type { Company, Equity, GrantBody } from "./types";

  // Stock options, RSUs and shares: what's vested and what it's worth, by company; from Carta or entered by hand.
  // `version` changes when the page loads again; `refresh` redraws the whole page (net worth counts vested equity).
  let { version, refresh }: { version: number; refresh: () => void } = $props();

  let d = $state<Equity | null>(null), error = $state("");
  async function load() {
    try { d = await api<Equity>("/api/equity"); error = ""; } catch (err) { error = (err as Error).message; }
  }
  // Load when shown, and again whenever the page loads.
  $effect(() => { void version; load(); });

  const c = $derived(d?.carta);
  const lastRead = $derived([c?.web_last, c?.last_sync].filter(Boolean).sort().at(-1));
  const chart = $derived(d?.companies.length ? vestingSeries(d.companies) : null);
  const CLS = ["s-main", "s-alt", "s-muted"] as const;
  const my = (s: string | null | undefined, o: Intl.DateTimeFormatOptions = { month: "short", year: "numeric" }) => (s ? fmtDate(s, o) : "");

  async function post(url: string, body: unknown, msg: string) {
    try { await api(url, { method: "POST", body }); if (msg) toast(msg); refresh(); }
    catch (err) { toast.error((err as Error).message); }
  }
  let adding = $state(false), coName = $state(""), coPrice = $state("");
  let syncing = $state(false);
  async function sync() {
    syncing = true;
    try {
      const r = await api<{ companies: number; grants: number }>("/api/carta/sync", { method: "POST" });
      toast(`Carta: ${r.companies} compan${r.companies === 1 ? "y" : "ies"}, ${r.grants} grant${r.grants === 1 ? "" : "s"}`);
    } catch (err) { toast.error((err as Error).message); }
    refresh();
  }
  // Which grant form is open: "new:<company>" or a grant's id.
  let form = $state<string | null>(null);
  const cid = (co: Company) => encodeURIComponent(co.id);
</script>

<Card.Root class="mb-6">
  <Card.Header>
    <Card.Title>Equity</Card.Title>
    <Card.Action class="flex flex-wrap gap-2">
      {#if c?.connected}<Button variant="outline" size="sm" disabled={syncing} onclick={sync}>{syncing ? "Reading Carta…" : "Sync from Carta"}</Button>{/if}
      <Button size="sm" onclick={() => { adding = true; coName = ""; coPrice = ""; }}>Add a company</Button>
    </Card.Action>
  </Card.Header>
  <Card.Content>
    {#if error && !d}<p class="text-sm text-destructive">{error}</p>
    {:else if !d || !c}<p class="text-sm text-muted-foreground">Loading…</p>
    {:else}
      <p class="text-sm text-muted-foreground">
        {#if lastRead}From Carta, last read {fmtDate(lastRead)}. {/if}Stock options, RSUs and shares: enter them here, or read them from Carta with
        Runway's browser extension (<a class="font-medium text-foreground underline underline-offset-4" href="#setup/connections">Settings → Connections</a>).
        Only what has vested counts toward net worth, at each company's latest share price (its 409A value, for a private company).
      </p>
      {#if c.last_error}
        <Alert.Root variant="destructive" class="mt-3"><TriangleAlert /><Alert.Description>Carta: {c.last_error}</Alert.Description></Alert.Root>
      {/if}
      {#if d.companies.length}
        <div class="mt-4 grid gap-4 sm:grid-cols-2">
          <div class="rounded-lg border border-border p-4">
            <div class="text-sm text-muted-foreground">Vested now</div>
            <div class="text-2xl font-semibold tabular-nums">{fmt0(d.vested_value)}</div>
            <div class="text-sm text-muted-foreground">{d.in_networth !== d.vested_value ? `${fmt0(d.in_networth)} counted in net worth` : "counted in net worth"}</div>
          </div>
          <div class="rounded-lg border border-border p-4">
            <div class="text-sm text-muted-foreground">Still to vest</div>
            <div class="text-2xl font-semibold tabular-nums">{fmt0(d.unvested_value)}</div>
            <div class="text-sm text-muted-foreground">at today's share prices</div>
          </div>
        </div>
        {#if chart}
          <div class="mt-4">
            <LineChart xs={chart.xs.map((x) => fmtDate(x, { month: "short", year: "numeric" }))} labels zero height={200} fmtY={shortMoney} fmtTip={fmt}
              series={chart.lines.map((l, i) => ({ ...l, cls: CLS[i], step: true }))} />
          </div>
        {/if}
      {/if}

      {#if adding}
        <div class="mt-4 flex flex-wrap items-end gap-3 rounded-lg bg-muted/40 p-4" data-editor>
          <label class="flex flex-col gap-1 text-sm">Company<Input class="w-56" bind:value={coName} placeholder="Acme, Inc." autofocus /></label>
          <label class="flex flex-col gap-1 text-sm">Share price
            <span class="relative">
              <span class="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true">$</span>
              <Input class="w-32 pl-6" type="number" min="0" step="0.01" value={coPrice} oninput={(e) => (coPrice = e.currentTarget.value)} placeholder="0.00" />
            </span>
          </label>
          <Button size="sm" onclick={() => post("/api/equity/companies", { name: coName, share_price: coPrice }, "Company added")}>Add</Button>
          <Button variant="link" size="sm" onclick={() => (adding = false)}>Cancel</Button>
        </div>
      {/if}

      {#each d.companies as co (co.id)}
        <div class="mt-5 border-t border-border pt-4">
          <div class="mb-2 flex flex-wrap items-center gap-3">
            <b>{co.name}</b>{#if co.source === "carta"}<Badge variant="secondary">Carta</Badge>{/if}
            <label class="inline-flex items-center gap-2 text-sm">Share price
              <span class="relative">
                <span class="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true">$</span>
                <input type="number" min="0" step="0.01" value={co.share_price ?? ""} placeholder="0.00"
                  class="h-8 w-28 rounded-md border border-input bg-transparent pr-2 pl-6 text-sm dark:bg-input/30"
                  use:autosave={(f) => post(`/api/equity/companies/${cid(co)}`, { share_price: f.value }, "Share price saved")} />
              </span>
            </label>
            <span class="text-sm text-muted-foreground">{co.price_as_of ? `as of ${fmtDate(co.price_as_of, { month: "short", day: "numeric", year: "numeric" })}` : "no price yet"}</span>
            <label class="inline-flex items-center gap-2 text-sm">
              <input type="checkbox" class="size-4" checked={!!co.in_networth}
                onchange={(e) => post(`/api/equity/companies/${cid(co)}`, { in_networth: e.currentTarget.checked }, "Saved")} /> Count in net worth
            </label>
            <span class="ml-auto inline-flex gap-1">
              <Button variant="link" size="sm" onclick={() => (form = `new:${co.id}`)}>Add a grant</Button>
              <ConfirmButton confirm="Remove it and its grants?" onconfirm={() => post(`/api/equity/companies/${cid(co)}/remove`, {}, "Removed")}>Remove</ConfirmButton>
            </span>
          </div>
          {#if co.grants.length}
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
                      <td class="min-w-32">
                        <div class="flex items-center gap-2">
                          <span class="h-1.5 flex-1 overflow-hidden rounded-full bg-muted"><i class="block h-full rounded-full bg-[var(--nw-1)]" style:width={`${(p * 100).toFixed(1)}%`}></i></span>
                          <span class="text-xs tabular-nums">{Math.round(p * 100)}%</span>
                        </div>
                        <div class="text-xs text-muted-foreground">{shares(g.vested)} of {shares(g.quantity)}{g.fully_vested_on && p < 1 ? ` · all by ${my(g.fully_vested_on)}` : ""}{g.exercised ? ` · ${shares(g.exercised)} exercised` : ""}</div>
                      </td>
                      <td class="text-right tabular-nums">{fmt(g.vested_value)}</td>
                      <td class="text-right text-muted-foreground tabular-nums max-[700px]:hidden">{fmt(g.unvested_value)}</td>
                      <td class="text-right whitespace-nowrap">
                        <Button variant="link" size="sm" class="px-1" aria-expanded={form === g.id} onclick={() => (form = form === g.id ? null : g.id)}>Edit</Button>
                        <ConfirmButton class="px-1" confirm="Remove?" onconfirm={() => post(`/api/equity/grants/${encodeURIComponent(g.id)}/remove`, {}, "Removed")}>Remove</ConfirmButton>
                      </td>
                    </tr>
                    {#if form === g.id}
                      <tr data-editor><td colspan="8">
                        <GrantForm {g} oncancel={() => (form = null)}
                          onsave={(body: GrantBody) => post(`/api/equity/grants/${encodeURIComponent(g.id)}`, body, "Grant saved")} />
                      </td></tr>
                    {/if}
                  {/each}
                </tbody>
              </table>
            </div>
          {:else}<p class="text-sm text-muted-foreground">No grants yet.</p>{/if}
          {#if form === `new:${co.id}`}
            <div data-editor>
              <GrantForm oncancel={() => (form = null)} onsave={(body: GrantBody) => post(`/api/equity/companies/${cid(co)}/grants`, body, "Grant added")} />
            </div>
          {/if}
        </div>
      {/each}
    {/if}
  </Card.Content>
</Card.Root>
