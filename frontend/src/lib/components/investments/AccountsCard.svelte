<script lang="ts">
  import * as Card from "$lib/components/ui/card";
  import { fmt } from "$lib/format";
  import { cn } from "$lib/utils";
  import { accountDayChange } from "./allocation";
  import { gainCls, signed } from "./numbers";
  import type { Holding, InvAccount, Investments } from "./types";
  import WorthALook from "./WorthALook.svelte";

  // Each visible investment account: its value and today's change, linking to the account in Settings → Accounts.
  let { accounts, holdings, checks = [] }: { accounts: InvAccount[]; holdings: Holding[]; checks?: Investments["xray"] } = $props();
  // The checks close the card: "All checks look fine", or the ones that need a look.
  const shown = $derived(accounts.filter((a) => !a.hidden));
  // Settings → Accounts knows an account by its own id: SimpleFIN's is the position id without "sf:", Plaid's the account_id it is matched to (none: the list without a row to open).
  const settingsId = (a: InvAccount) => (a.source === "simplefin" ? a.id.slice(3) : a.account_id);
  const link = (a: InvAccount) => { const id = settingsId(a); return id ? `#setup/accounts?account=${encodeURIComponent(id)}` : "#setup/accounts"; };
  // The value is the account's balance (which can include cash); today's change comes only from the holdings' lots, so the two can cover different amounts. With no balance, the lots' values stand in.
  const value = (a: InvAccount) => a.balance ?? holdings.reduce((s, h) => s + h.lots.filter((l) => l.account_id === a.id).reduce((t, l) => t + l.value, 0), 0);
</script>

{#if shown.length}
  <Card.Root data-testid="accounts-card">
    <Card.Header><Card.Title>Accounts</Card.Title></Card.Header>
    <Card.Content>
      <ul>
        {#each shown as a (a.id)}
          {@const day = accountDayChange(a, holdings)}
          <li class="border-t border-border first:border-t-0">
            <a href={link(a)} class="flex min-h-11 items-center gap-3 py-2 hover:underline" data-testid="account-row">
              <span class="min-w-0 flex-1">
                <span class="block truncate text-sm">{a.name || a.official_name || "Account"}</span>
                {#if a.institution_name}<span class="block truncate text-xs text-muted-foreground">{a.institution_name}{a.mask ? ` ···${a.mask}` : ""}</span>{/if}
              </span>
              <span class="text-right tabular-nums">
                <span class="block text-sm">{fmt(value(a))}</span>
                <span class={cn("block text-xs text-muted-foreground", gainCls(day))} data-testid="account-day">{day == null ? "—" : `${signed(day)} today`}</span>
              </span>
            </a>
          </li>
        {/each}
      </ul>
      <WorthALook {checks} inline />
    </Card.Content>
  </Card.Root>
{/if}
