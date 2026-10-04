<script lang="ts">
  import { errMsg } from "$lib/act";
  import { reload } from "$lib/app.svelte";
  import { api } from "$lib/api";
  import { Button } from "$lib/components/ui/button";
  import { fmt, fmtDateTime, nb } from "$lib/format";
  import { toast } from "svelte-sonner";
  import { linkable, linkableInvestments, plaidLabel, type sourceInfo } from "./plaidAccounts";
  import { connectPlaid, matchPlaidAccount } from "./plaid.svelte";
  import PlaidChoice from "./PlaidChoice.svelte";
  import type { PlaidStatus, SettingsAccount } from "./types";
  import { fieldCls, rowCls, selectCls } from "./ui";

  // An account's data source, as a section of its row (it sits in the row's grid): SimpleFIN, Plaid, or both, with the
  // choice between them once the account is matched to a Plaid account, and linking one that isn't. `info` is what
  // plaidAccounts.sourceInfo says about the account (the row's summary line reads the same).
  let { a, plaid, mine, info }: { a: SettingsAccount; plaid: PlaidStatus | null; mine: SettingsAccount[]; info: ReturnType<typeof sourceInfo> } = $props();

  const options = $derived(linkable(plaid));
  const invOptions = $derived(linkableInvestments(plaid, a.id));
  const plaidSynced = $derived.by(() => {
    const t = info.behind?.it.last_sync;
    if (!t) return "";
    const d = new Date(t.replace(" ", "T") + "Z");
    return isNaN(d.getTime()) ? t : fmtDateTime(d);
  });

  async function setProvider(e: Event) {
    const v = (e.currentTarget as HTMLSelectElement).value;
    try {
      await api(`/api/accounts/${encodeURIComponent(a.id)}`, { method: "POST", body: { provider: v } });
      toast.success(v === "plaid" ? "This account now comes from Plaid; its transactions arrive with the next sync" : "Back to SimpleFIN");
      if (v === "plaid") api("/api/sync", { method: "POST" }).then(() => reload(), () => {});
    } catch (err) { toast.error(errMsg(err)); reload(); }
  }

  let linking = $state("");
  async function linkTo(e: Event) {
    const el = e.currentTarget as HTMLSelectElement;
    const v = el.value;
    if (!v) return;
    linking = v;
    if (v === "__connect") await connectPlaid(info.invKind ? "investments" : "bank");
    else await matchPlaidAccount(v, a.id, !info.invKind);
    linking = ""; el.value = "";
  }
</script>

{#if info.showSource}
  <section class="flex flex-col gap-3 rounded-lg border p-3 sm:col-span-2 lg:col-span-3" aria-label="Data source">
    <h4 class="text-sm font-medium">Data source</h4>
    <p class="text-sm text-muted-foreground">{nb(info.source)}{#if plaidSynced}{" · "}{nb(`Plaid synced ${plaidSynced}`)}{/if}</p>
    <div class={rowCls}>
      {#if info.canSwitch}
        <label class={fieldCls} title="Where balances and transactions come from. Switching keeps your history; transactions both have are matched up.">
          Transactions from
          <select class={selectCls} value={a.provider === "plaid" ? "plaid" : "simplefin"} onchange={setProvider}>
            <option value="simplefin">SimpleFIN</option>
            <option value="plaid">Plaid ({info.where})</option>
          </select>
        </label>
      {/if}
      {#if info.own && info.behind}
        <label class={fieldCls}>Which of your accounts this is
          <PlaidChoice p={info.behind.p} it={info.behind.it} {mine} />
        </label>
      {:else if info.invLinked}
        <span class="flex items-center gap-2 text-sm">Linked to {plaidLabel(info.invLinked)}
          <Button variant="outline" size="sm" onclick={() => matchPlaidAccount(info.invLinked!.p.id, "", false)}>Unlink</Button></span>
      {:else if info.link && a.plaid_account_id}
        <span class="flex items-center gap-2 text-sm">Linked to {info.where}
          <Button variant="outline" size="sm" onclick={() => matchPlaidAccount(a.plaid_account_id!, "")}>Unlink</Button></span>
      {:else if info.invKind}
        <label class={fieldCls}>Link to a Plaid account
          <select class={`${selectCls} w-full sm:w-72`} disabled={!!linking} onchange={linkTo}>
            <option value="">Choose…</option>
            {#each invOptions as o (o.p.id)}<option value={o.p.id}>{plaidLabel(o)} · {fmt(o.p.balance)}</option>{/each}
            {#if plaid?.configured}<option value="__connect">Connect an investment account through Plaid…</option>{/if}
          </select>
        </label>
      {:else if !info.link && info.linkKind}
        <label class={fieldCls}>Link to a Plaid account
          <select class={`${selectCls} w-full sm:w-72`} disabled={!!linking} onchange={linkTo}>
            <option value="">Choose…</option>
            {#each options as o (o.p.id)}<option value={o.p.id}>{plaidLabel(o)} · {fmt(o.p.balance)}</option>{/each}
            {#if plaid?.configured}<option value="__connect">Connect a new bank through Plaid…</option>{/if}
          </select>
        </label>
      {/if}
    </div>
  </section>
{/if}
