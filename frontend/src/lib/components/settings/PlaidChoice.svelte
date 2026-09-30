<script lang="ts">
  import { accountName } from "$lib/types";
  import { cn } from "$lib/utils";
  import { matchPlaidAccount } from "./plaid.svelte";
  import type { PlaidAccount, SettingsAccount } from "./types";
  import { selectCls } from "./ui";

  // What a Plaid bank account is: added as its own account, the same as one of yours, or not used.
  let { p, mine, class: cls = "" }: { p: PlaidAccount; mine: SettingsAccount[]; class?: string } = $props();

  const sel = $derived(p.ignored ? "ignore" : p.account_id || "");
  const own = $derived(!!p.account_id?.startsWith("pl:"));
  // Yours that could be this one: not from Plaid themselves, and not already linked to another Plaid account.
  const options = $derived(mine.filter((a) => !a.id.startsWith("pl:") && ["checking", "savings", "credit", "loan"].includes(a.kind)
    && (!a.plaid_account_id || a.plaid_account_id === p.id)));
</script>

<select class={cn(selectCls, "w-full sm:w-60", sel && sel !== "ignore" && "border-transparent shadow-none dark:bg-transparent hover:border-input", cls)}
  aria-label="Which of your accounts this is" value={sel} onchange={(e) => matchPlaidAccount(p.id, e.currentTarget.value)}>
  <option value="">Choose…</option>
  {#if own}<option value={p.account_id}>Its own account</option>{:else}<option value="new">Add as its own account</option>{/if}
  {#each options as a (a.id)}<option value={a.id}>Same as {accountName(a)}</option>{/each}
  <option value="ignore">Don't use</option>
</select>
