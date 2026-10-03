<script lang="ts" module>
  /** `synced`: a loan paid down since its last balance (runway/loans.py owed_on), what that balance was. */
  export interface PanelAccount { id: string; name: string; org: string | null; balance: number; as_of?: string | null; synced?: number; counted: boolean }
</script>

<script lang="ts">
  import * as Sheet from "$lib/components/ui/sheet";
  import { fmt, fmtDate } from "$lib/format";

  // The side panel for one bank account: its balance and whether it counts toward net worth. `open` is bound so the page
  // can open it. Flipping the switch calls `onchange`; the page saves it and hands back the updated account.
  let { open = $bindable(false), acct, onchange }: { open?: boolean; acct: PanelAccount | null; onchange: (counted: boolean) => void } = $props();
</script>

<Sheet.Root bind:open>
  <Sheet.Content>
    {#if acct}
      <Sheet.Header>
        <Sheet.Title>{acct.name}</Sheet.Title>
        <Sheet.Description>{acct.org ?? "Account"}</Sheet.Description>
      </Sheet.Header>
      <div class="grid gap-4 px-4 pb-4 text-sm">
        <dl class="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1">
          <dt class="text-muted-foreground">Balance</dt><dd class="text-right font-semibold tabular-nums">{fmt(acct.balance)}</dd>
          {#if acct.as_of}<dt class="text-muted-foreground">Last synced</dt><dd class="text-right">{fmtDate(acct.as_of, { month: "short", day: "numeric", year: "numeric" })}</dd>{/if}
          {#if acct.synced != null}<dt class="text-muted-foreground" title="The balance the lender last sent, before the payments since then">Synced balance</dt><dd class="text-right tabular-nums">{fmt(acct.synced)}</dd>{/if}
        </dl>
        <div class="flex items-center justify-between gap-4 rounded-lg border border-border p-3">
          <span id="count-in-nw" class="font-medium">Count in net worth</span>
          <button type="button" role="switch" aria-checked={acct.counted} aria-labelledby="count-in-nw" onclick={() => onchange(!acct.counted)}
            class="inline-flex h-6 w-11 shrink-0 cursor-pointer items-center rounded-full border border-transparent p-0.5 transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none {acct.counted ? 'bg-primary' : 'bg-input'}">
            <span class="block size-5 rounded-full bg-background shadow transition-transform {acct.counted ? 'translate-x-5' : 'translate-x-0'}"></span>
          </button>
        </div>
        <a href="#setup/accounts" class="font-medium underline underline-offset-4">More account settings →</a>
      </div>
    {/if}
  </Sheet.Content>
</Sheet.Root>
