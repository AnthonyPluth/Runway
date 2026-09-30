<script lang="ts">
  import { fmt } from "$lib/format";
  import { accountName } from "$lib/types";
  import { cn } from "$lib/utils";
  import { matchPlaidAccount } from "./plaid.svelte";
  import type { PlaidAccount, PlaidItem, SettingsAccount } from "./types";
  import { selectCls } from "./ui";

  // What a Plaid account is: added as its own account, the same as one of yours, or not used. An investment connection's
  // accounts (`it` without `bank`) choose among that connection's candidates instead of your bank and card accounts.
  let { p, mine, it = null, class: cls = "" }: { p: PlaidAccount; mine: SettingsAccount[]; it?: PlaidItem | null; class?: string } = $props();

  const inv = $derived(!!it && !it.bank);
  const sel = $derived(p.ignored ? "ignore" : p.account_id || "");
  const own = $derived(!!p.account_id?.startsWith("pl:"));
  // Yours that could be this one: not from Plaid themselves, and not already linked to another Plaid account.
  const options = $derived(mine.filter((a) => !a.id.startsWith("pl:") && ["checking", "savings", "credit", "loan"].includes(a.kind)
    && (!a.plaid_account_id || a.plaid_account_id === p.id)));
  const candidates = $derived((it?.candidates ?? []).filter((a) => !a.linked_to || a.linked_to === p.id));
</script>

<select class={cn(selectCls, "w-full sm:w-60", sel && sel !== "ignore" && "border-transparent shadow-none dark:bg-transparent hover:border-input", cls)}
  aria-label="Which of your accounts this is" value={inv && own ? "new" : sel} onchange={(e) => matchPlaidAccount(p.id, e.currentTarget.value, !inv)}>
  <option value="">Choose…</option>
  {#if inv}
    <option value="new">{own ? "Its own account" : "Add as a new account"}</option>
    {#each candidates as a (a.id)}<option value={a.id}>Same as {a.display_name || a.name} ({fmt(a.balance)})</option>{/each}
    <option value="ignore">Don't count it</option>
  {:else}
    {#if own}<option value={p.account_id}>Its own account</option>{:else}<option value="new">Add as its own account</option>{/if}
    {#each options as a (a.id)}<option value={a.id}>Same as {accountName(a)}</option>{/each}
    <option value="ignore">Don't use</option>
  {/if}
</select>
