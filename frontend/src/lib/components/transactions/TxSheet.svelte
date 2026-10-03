<script lang="ts">
  import type { RecurringItem } from "$lib/components/recurring/types";
  import * as Sheet from "$lib/components/ui/sheet";
  import type { Account } from "$lib/types";
  import TxAdd from "./TxAdd.svelte";
  import TxDetail from "./TxDetail.svelte";
  import type { Tx } from "./types";

  // A transaction's details, to see and change (`t`), or a new one to add (`t` null): a bottom sheet on a phone, a
  // panel on the right on a wider screen.
  let { open = $bindable(false), t = null, accounts, account = "", recurring, family, onsave, onchanged, onpatched }: {
    open?: boolean; t?: Tx | null; accounts: Account[]; account?: string; recurring: RecurringItem[]; family?: string[];
    onsave: (t: Tx, category: string) => Promise<boolean | void>; onchanged: () => void; onpatched?: (t: Partial<Tx>) => void;
  } = $props();
  let panel = $state<HTMLElement | null>(null);
</script>

<!-- Opened on a transaction, focus goes to the sheet itself, not its first field (a phone's keyboard would cover it). -->
<Sheet.Root bind:open>
  <Sheet.Content bind:ref={panel} onOpenAutoFocus={(e) => { if (t) { e.preventDefault(); panel?.focus(); } }}>
    {#if t}
      {#key t.id}<TxDetail {t} {recurring} {family} {onsave} {onchanged} {onpatched} onclose={() => (open = false)} />{/key}
    {:else if open}
      <TxAdd {accounts} {account} {onchanged} onclose={() => (open = false)} />
    {/if}
  </Sheet.Content>
</Sheet.Root>
