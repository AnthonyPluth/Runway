<script lang="ts">
  import { isFiltered, type TxFilters } from "$lib/filters.svelte";
  import type { LoadedAccount } from "$lib/accounts";
  import { accountName } from "$lib/types";
  import { cn } from "$lib/utils";
  import ChevronDown from "@lucide/svelte/icons/chevron-down";

  // The chips under All's search: All (no filters), To review · N (opens the Review tab), Pending (the pending
  // rows among those loaded) and Accounts (the account filter, a native picker). They set the page's own filters
  // (filters.svelte.ts); there is no filtering of their own except Pending. They scroll sideways when they don't fit.
  let { f, accounts, reviewCount = 0, pending = $bindable(false), onall, onaccount }: {
    f: TxFilters; accounts: LoadedAccount[]; reviewCount?: number;
    pending?: boolean; onall: () => void; onaccount: () => void;
  } = $props();

  const chip = (on: boolean) => cn("relative inline-flex h-9 shrink-0 cursor-pointer items-center gap-1 rounded-full border px-3.5 text-sm whitespace-nowrap transition-colors phone:h-11",
    on ? "border-primary bg-primary/15 font-medium text-primary" : "border-transparent bg-muted text-foreground hover:border-muted-foreground/40");
  const account = $derived(accounts.find((a) => a.id === f.account));
</script>

<div role="group" aria-label="Filter transactions" data-chips class="-mx-4 mb-3 flex gap-2 overflow-x-auto px-4 pb-1 [scrollbar-width:none] md:mx-0 md:px-0 [&::-webkit-scrollbar]:hidden">
  <button type="button" class={chip(!isFiltered(f) && !pending)} aria-pressed={!isFiltered(f) && !pending} onclick={onall}>All</button>
  <a href="#review" class={chip(false)}>To review{reviewCount ? ` · ${reviewCount}` : ""}</a>
  <button type="button" class={chip(pending)} aria-pressed={pending} onclick={() => (pending = !pending)}>Pending</button>
  <label class={chip(!!f.account)}>
    <span>{account ? accountName(account) : "Accounts"}</span><ChevronDown class="size-4 opacity-60" aria-hidden="true" />
    <!-- The existing account filter, laid invisibly over the chip so it opens the system's picker. -->
    <select aria-label="Account" class="absolute inset-0 size-full cursor-pointer opacity-0" bind:value={f.account} onchange={onaccount}>
      <option value="">All accounts</option>
      {#each accounts as a (a.id)}<option value={a.id}>{accountName(a)}</option>{/each}
    </select>
  </label>
</div>
