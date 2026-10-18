<script lang="ts">
  import type { TxFilters } from "$lib/filters.svelte";
  import type { LoadedAccount } from "$lib/accounts";
  import { accountName } from "$lib/types";
  import { cn } from "$lib/utils";
  import ChevronDown from "@lucide/svelte/icons/chevron-down";

  // The Accounts chip under All's search (the account filter, a native picker). It sets the page's own account filter
  // (filters.svelte.ts). There are no All, To review or Pending chips: the To review tab and the card's See all open
  // the review list, and Clear filters resets the rest.
  let { f, accounts, onaccount }: { f: TxFilters; accounts: LoadedAccount[]; onaccount: () => void } = $props();

  const chip = (on: boolean) => cn("relative inline-flex h-9 shrink-0 cursor-pointer items-center gap-1 rounded-full border px-3.5 text-sm whitespace-nowrap transition-colors phone:h-11",
    on ? "border-primary bg-primary/15 font-medium text-primary" : "border-transparent bg-muted text-foreground hover:border-muted-foreground/40");
  const account = $derived(accounts.find((a) => a.id === f.account));
</script>

<div role="group" aria-label="Filter transactions" data-chips class="-mx-4 mb-3 flex gap-2 overflow-x-auto px-4 pb-1 [scrollbar-width:none] md:mx-0 md:px-0 [&::-webkit-scrollbar]:hidden">
  <label class={chip(!!f.account)}>
    <span>{account ? accountName(account) : "Accounts"}</span><ChevronDown class="size-4 opacity-60" aria-hidden="true" />
    <!-- The existing account filter, laid invisibly over the chip so it opens the system's picker. -->
    <select aria-label="Account" class="absolute inset-0 size-full cursor-pointer opacity-0" bind:value={f.account} onchange={onaccount}>
      <option value="">All accounts</option>
      {#each accounts as a (a.id)}<option value={a.id}>{accountName(a)}</option>{/each}
    </select>
  </label>
</div>
