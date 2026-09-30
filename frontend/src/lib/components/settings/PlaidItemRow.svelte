<script lang="ts">
  import { api } from "$lib/api";
  import { reload } from "$lib/app.svelte";
  import ConfirmButton from "$lib/components/ConfirmButton.svelte";
  import { Badge } from "$lib/components/ui/badge";
  import { Button } from "$lib/components/ui/button";
  import { fmt, nb } from "$lib/format";
  import { cn } from "$lib/utils";
  import { toast } from "svelte-sonner";
  import BankIcon from "./BankIcon.svelte";
  import { matchPlaidAccount, openPlaidLink } from "./plaid.svelte";
  import type { PlaidAccount, PlaidItem } from "./types";
  import { linkCls, selectCls, warnText } from "./ui";

  // A Plaid connection: its bank, when it last synced (or what's wrong), Sync/Reconnect and Remove. Bank and card accounts
  // are matched to yours under Settings → Accounts; an investment connection's accounts are still matched here.
  let { it, items }: { it: PlaidItem; items: PlaidItem[] } = $props();

  const utc = (t: string) => new Date(t.replace(" ", "T") + "Z");
  // Two connections to the same institution look alike, so each says when it was made (with the time, if there's a twin).
  const connectedOn = $derived.by(() => {
    const d = it.created_at ? utc(it.created_at) : null;
    if (!d || isNaN(d.getTime())) return "";
    const twin = items.some((o) => o !== it && (o.institution_name || "") === (it.institution_name || ""));
    const opts: Intl.DateTimeFormatOptions = twin ? { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" } : { month: "short", day: "numeric", year: "numeric" };
    return `connected ${d.toLocaleString("en-US", opts)} · `;
  });
  const synced = $derived.by(() => {
    const t = it.last_sync;
    if (!t) return "not synced";
    const d = utc(t);
    return isNaN(d.getTime()) ? `synced ${t}` : `synced ${d.toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}`;
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

  const bankAccounts = $derived(it.accounts.filter((p) => p.type !== "investment"));
  const waiting = $derived(bankAccounts.filter((p) => !p.account_id && !p.ignored).length);
  const selected = (p: PlaidAccount) => p.account_id || "";

  let syncing = $state(false);
  async function sync() {
    syncing = true;
    try {
      const r = await api<{ bank?: boolean; new_transactions?: number; statements?: number; holdings?: number; transactions?: number }>(
        `/api/plaid/items/${encodeURIComponent(it.item_id)}/sync`, { method: "POST" });
      toast.success(r.bank ? `Synced · ${r.new_transactions} new transactions · ${r.statements} statements` : `Synced ${r.holdings} holdings, ${r.transactions} activities`);
    } catch (err) { toast.error((err as Error).message); }
    reload();
  }
  async function reconnect() {
    try { if (await openPlaidLink(it.item_id, "update")) reload(); } catch (err) { toast.error((err as Error).message); }
  }
  async function remove() {
    try { await api(`/api/plaid/items/${encodeURIComponent(it.item_id)}/remove`, { method: "POST" }); toast.success("Connection removed"); reload(); }
    catch (err) { toast.error((err as Error).message); }
  }
</script>

{#snippet account(p: PlaidAccount, kind: string)}
  {@const sel = selected(p)}
  <div class="flex flex-wrap items-center gap-3 border-t py-2">
    <span class="flex min-w-0 flex-1 flex-col text-sm">
      <span>{p.name || p.official_name || "Account"}{#if p.mask}{" "}<span class="text-muted-foreground">••{p.mask}</span>{/if}</span>
      <span class="text-xs text-muted-foreground">{nb(kind)} · {nb(fmt(p.balance))}</span>
    </span>
    <select class={cn(selectCls, "w-full sm:ml-auto sm:w-60", sel && sel !== "ignore" && "border-transparent shadow-none dark:bg-transparent hover:border-input")}
      aria-label="Which of your accounts this is" value={sel.startsWith("pl:") ? "new" : sel} onchange={(e) => matchPlaidAccount(p.id, e.currentTarget.value, false)}>
      <option value="">Choose…</option>
      {#if sel.startsWith("pl:")}<option value="new">Its own account</option>{:else}<option value="new">Add as a new account</option>{/if}
      {#each (it.candidates || []).filter((a) => !a.linked_to || a.linked_to === p.id) as a (a.id)}
        <option value={a.id}>Same as {a.display_name || a.name} ({fmt(a.balance)})</option>
      {/each}
      <option value="ignore">Don't count it</option>
    </select>
  </div>
{/snippet}

<div class="rounded-xl border px-3 py-2.5">
  <div class="flex flex-wrap items-center gap-3">
    <BankIcon name={it.institution_name} />
    <span class="flex min-w-0 flex-1 flex-col">
      <span class="flex flex-wrap items-center gap-1.5 font-medium">{it.institution_name || "Connection"}
        {#if it.env === "sandbox"}<Badge variant="secondary">sandbox</Badge>{/if}
        <Badge variant="secondary">{it.bank ? (it.products.includes("transactions") ? "bank" : "card statements") : "investments"}</Badge></span>
      <span class="text-xs text-muted-foreground">{connectedOn}{#if it.error}<span class={warnText}>{it.error === "ITEM_LOGIN_REQUIRED" ? "Login expired; reconnect to fix" : it.error}</span>{:else}{synced}{/if}</span>
    </span>
    <span class="flex items-center gap-1 whitespace-nowrap">
      {#if it.error}<Button size="sm" onclick={reconnect}>Reconnect</Button>
      {:else}<Button variant="outline" size="sm" disabled={syncing} onclick={sync}>{syncing ? "Syncing…" : "Sync"}</Button>{/if}
      <ConfirmButton confirm="Remove this connection?" onconfirm={remove}>Remove</ConfirmButton>
    </span>
  </div>
  {#if duplicate}<p class={cn("mt-2 text-sm sm:ml-10", warnText)}>{duplicate}</p>{/if}
  <div class="mt-1.5 sm:ml-10">
    {#if it.bank}
      <p class="border-t pt-2 text-sm text-muted-foreground">{bankAccounts.length === 1 ? "1 account" : `${bankAccounts.length} accounts`}{#if waiting}{" · "}<a class={linkCls}
        href="#setup/accounts">{waiting} {waiting === 1 ? "needs" : "need"} a decision →</a>{:else}{" · "}<a class={linkCls} href="#setup/accounts">Manage in Accounts</a>{/if}</p>
    {:else}
      {#each it.accounts as p (p.id)}{@render account(p, p.subtype || "investment")}{:else}<p class="text-xs text-muted-foreground">no accounts yet</p>{/each}
    {/if}
  </div>
</div>
