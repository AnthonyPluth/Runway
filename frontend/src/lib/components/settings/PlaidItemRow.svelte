<script lang="ts">
  import { api } from "$lib/api";
  import { reload } from "$lib/app.svelte";
  import { Badge } from "$lib/components/ui/badge";
  import { Button } from "$lib/components/ui/button";
  import { ConfirmDialog } from "$lib/components/ui/confirm-dialog";
  import { fmtDateTime, plural } from "$lib/format";
  import { cn } from "$lib/utils";
  import { toast } from "svelte-sonner";
  import BankIcon from "./BankIcon.svelte";
  import { openPlaidLink } from "./plaid.svelte";
  import { plaidProblem } from "./plaidErrors";
  import type { PlaidItem } from "./types";
  import { linkCls, warnText } from "./ui";

  // A Plaid connection: its bank, when it last synced (or what's wrong), Sync/Reconnect and Remove. Its accounts are
  // matched to yours under Settings → Accounts.
  let { it, items }: { it: PlaidItem; items: PlaidItem[] } = $props();

  const utc = (t: string) => new Date(t.replace(" ", "T") + "Z");
  // Two connections to the same institution look alike, so each says when it was made (with the time, if there's a twin).
  const connectedOn = $derived.by(() => {
    const d = it.created_at ? utc(it.created_at) : null;
    if (!d || isNaN(d.getTime())) return "";
    const twin = items.some((o) => o !== it && (o.institution_name || "") === (it.institution_name || ""));
    return `connected ${twin ? fmtDateTime(d) : d.toLocaleString("en-US", { month: "short", day: "numeric", year: "numeric" })} · `;
  });
  // Beside an error, the time is when it last synced without one.
  const synced = $derived.by(() => {
    const t = it.last_sync;
    if (!t) return it.error ? "" : "not synced";
    const d = utc(t);
    const when = isNaN(d.getTime()) ? t : fmtDateTime(d);
    return it.error ? `last successful sync ${when}` : `synced ${when}`;
  });
  const duplicate = $derived.by(() => {
    const d = (it.duplicates || [])[0];
    if (!d) return "";
    const other = items.find((o) => o.item_id === d.item_id);
    const same = d.shared === it.accounts.length && d.shared === (other?.accounts.length ?? -1);
    const inst = it.institution_name || "";
    return `${same ? `Same ${d.shared === 1 ? "account" : `${d.shared} accounts`} as the other ${inst} connection`
      : `${d.shared} of these accounts ${d.shared === 1 ? "is" : "are"} also in the other ${inst} connection`}, so ${d.shared === 1 ? "it's" : "they're"} counted twice. Remove one of the two.`;
  });

  // Bank and card accounts wait until matched or left out (ignored); an investment account is left out as account_id "ignore".
  const accounts = $derived(it.bank ? it.accounts.filter((p) => p.type !== "investment") : it.accounts);
  const waiting = $derived(accounts.filter((p) => !p.account_id && !p.ignored).length);
  const problem = $derived(it.error ? plaidProblem(it.error) : null);
  // A plain bank connection needs no label; one that only brings in card statements, or investments, says so.
  const kind = $derived(it.bank ? (it.products.includes("transactions") ? "" : "card statements") : "investments");

  // What Remove does (plaid.remove_item): an investment connection's accounts go with their holdings and activity; a bank
  // connection's accounts that SimpleFIN also has go back to it, and the ones only Plaid had keep their history.
  const n = (k: number, one: string, many: string) => `${k} ${k === 1 ? one : many}`;
  const investments = $derived(it.bank ? 0 : it.accounts.length);
  const viaSimplefin = $derived(it.bank ? accounts.filter((p) => p.account_id && !p.account_id.startsWith("pl:")).length : 0);
  const plaidOnly = $derived(it.bank ? accounts.filter((p) => p.account_id?.startsWith("pl:")).length : 0);
  let removing = $state(false);

  let syncing = $state(false);
  async function sync() {
    syncing = true;
    try {
      const r = await api<{ bank?: boolean; new_transactions?: number; statements?: number; holdings?: number; transactions?: number }>(
        `/api/plaid/items/${encodeURIComponent(it.item_id)}/sync`, { method: "POST" });
      toast.success(r.bank ? `Synced · ${plural(r.new_transactions ?? 0, "new transaction")} · ${plural(r.statements ?? 0, "statement")}`
        : `Synced ${plural(r.holdings ?? 0, "holding")}, ${r.transactions ?? 0} ${r.transactions === 1 ? "activity" : "activities"}`);
    } catch (err) { toast.error((err as Error).message); }
    reload();
  }
  async function reconnect() {
    try { if (await openPlaidLink(it.item_id, "update")) reload(); } catch (err) { toast.error((err as Error).message); }
  }
  async function remove() {
    try { await api(`/api/plaid/items/${encodeURIComponent(it.item_id)}/remove`, { method: "POST" }); toast.success("Connection removed"); reload(); }
    catch (err) { toast.error((err as Error).message); return false; }
  }
</script>

<div class="rounded-xl border px-3 py-2.5">
  <div class="flex flex-wrap items-center gap-3">
    <BankIcon name={it.institution_name} />
    <span class="flex min-w-0 flex-1 flex-col">
      <span class="flex flex-wrap items-center gap-1.5 font-medium">{it.institution_name || "Connection"}
        {#if it.env === "sandbox"}<Badge variant="secondary">sandbox</Badge>{/if}
        {#if kind}<Badge variant="secondary">{kind}</Badge>{/if}</span>
      <span class="text-xs text-muted-foreground">{connectedOn}{synced}{#if problem}{synced ? " · " : ""}<span
        class="text-warning">{problem.text}</span>{/if}</span>
      {#if problem?.detail}
        <details class="text-xs text-muted-foreground">
          <summary class="w-fit cursor-pointer select-none hoverable:hover:text-foreground">Details</summary>
          <p class="mt-0.5 font-mono break-words select-all">{problem.detail}</p>
        </details>
      {/if}
    </span>
    <span class="flex items-center gap-1 whitespace-nowrap">
      {#if problem?.reconnect}<Button size="sm" onclick={reconnect}>Reconnect</Button>
      {:else}<Button variant="outline" size="sm" disabled={syncing} onclick={sync}>{syncing ? "Syncing…" : "Sync"}</Button>{/if}
      <Button variant="link" size="sm" onclick={() => (removing = true)}>Remove</Button>
    </span>
  </div>
  {#if duplicate}<p class={cn("mt-2 text-sm sm:ml-10", warnText)}>{duplicate}</p>{/if}
  <div class="mt-1.5 sm:ml-10">
    <p class="border-t pt-2 text-sm text-muted-foreground">{#if !accounts.length}no accounts yet{:else}{accounts.length === 1 ? "1 account" : `${accounts.length} accounts`}{#if waiting}{" · "}<a class={linkCls}
      href="#setup/accounts">{waiting} {waiting === 1 ? "needs" : "need"} a decision →</a>{:else}{" · "}<a class={linkCls} href="#setup/accounts">Manage in Accounts</a>{/if}{/if}</p>
  </div>
</div>

<ConfirmDialog bind:open={removing} title={`Remove ${it.institution_name || "this connection"}?`} confirmLabel="Remove" busyLabel="Removing…"
  destructive onconfirm={remove}>
  {#snippet description()}
    <p>Revokes Runway’s access at Plaid. You’d have to connect it again from scratch.</p>
    {#if investments || viaSimplefin || plaidOnly}
      <ul class="list-disc space-y-1 pl-5">
        {#if investments}<li>Deletes {n(investments, "investment account", "investment accounts")} with {investments === 1 ? "its" : "their"} holdings and activity.</li>{/if}
        {#if viaSimplefin}<li>{n(viaSimplefin, "account that also comes", "accounts that also come")} through SimpleFIN {viaSimplefin === 1 ? "keeps" : "keep"} syncing there.</li>{/if}
        {#if plaidOnly}<li>{n(plaidOnly, "account only Plaid had keeps its", "accounts only Plaid had keep their")} history but {plaidOnly === 1 ? "stops" : "stop"} updating.</li>{/if}
      </ul>
    {/if}
    <p><a class={linkCls} href="#setup/advanced" onclick={() => (removing = false)}>Download a backup first</a></p>
  {/snippet}
</ConfirmDialog>
