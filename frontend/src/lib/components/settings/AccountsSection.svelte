<script lang="ts">
  import { isBankKind, isCash, isPlaidStub, KIND_GROUPS } from "$lib/accounts";
  import { api } from "$lib/api";
  import { reload } from "$lib/app.svelte";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { plural } from "$lib/format";
  import { toast } from "svelte-sonner";
  import { accountName } from "$lib/types";
  import { accountFocus } from "./accountFocus.svelte";
  import AccountRow from "./AccountRow.svelte";
  import NewFromPlaid from "./NewFromPlaid.svelte";
  import { ignoredAccounts, undecidedAccounts } from "./plaidAccounts";
  import type { DeletedAccount, PlaidStatus, SettingsAccount } from "./types";
  import { linkCls } from "./ui";
  import { act } from "$lib/act";

  // Settings → Accounts: every account grouped by type. The forecast account can be chosen here or on Overview (its
  // forecast settings, which also hold its length). Hiding or showing an account moves its row here, without loading
  // the page again: the list stays where it is, and the hidden accounts' count changes.
  let { accounts }: { accounts: SettingsAccount[] } = $props();
  let hiddenNow = $state<Record<string, boolean>>({});
  const moved = (id: string, on: boolean) => { hiddenNow = { ...hiddenNow, [id]: on }; };
  const list = $derived(accounts.map((a) => (a.id in hiddenNow ? { ...a, hidden: hiddenNow[a.id] } : a)));

  const cash = $derived(list.filter((a) => isCash(a.kind)));
  const byName = $derived(Object.fromEntries(accounts.map((a) => [a.id, accountName(a)])));

  const groups = $derived(KIND_GROUPS.map(([title, kinds]) => ({ title, list: list.filter((a) => !a.hidden && (kinds as readonly string[]).includes(a.kind)) })).filter((g) => g.list.length));
  // Hidden accounts are tucked into a collapsed line, like the deleted ones below; a link to one of them opens it.
  const hiddenList = $derived(list.filter((a) => a.hidden));
  let showHidden = $state(false);
  const hiddenOpen = $derived(showHidden || hiddenList.some((a) => a.id === accountFocus.id));

  // Plaid's accounts, for the "New from Plaid" group and for linking an account to one. Without Plaid this just stays null.
  let plaid = $state<PlaidStatus | null>(null);
  api<PlaidStatus>("/api/plaid/status").then((r) => (plaid = r), () => {});
  const waiting = $derived(undecidedAccounts(plaid));
  const mine = $derived(accounts.filter((a) => !isPlaidStub(a.id) && isBankKind(a.kind)));

  // Accounts you deleted, which syncs leave out until you restore one (runway/deleted_accounts.py).
  let deleted = $state<DeletedAccount[]>([]);
  api<DeletedAccount[]>("/api/accounts/deleted").then((r) => (deleted = Array.isArray(r) ? r : []), () => {});
  let showDeleted = $state(false);
  let restoring = $state("");
  async function restore(d: DeletedAccount) {
    await act(async () => {
      await api(`/api/accounts/${encodeURIComponent(d.id)}/restore`, { method: "POST" });
      toast.success(`${d.name || "The account"} comes back with the next sync`);
      reload();
    }, { busy: (on) => (restoring = on ? d.id : "") });
  }
</script>

<NewFromPlaid {waiting} left={ignoredAccounts(plaid)} {mine} />

<Card.Root>
  <Card.Header class="px-4 sm:px-6"><Card.Title>Accounts</Card.Title></Card.Header>
  <Card.Content class="px-4 sm:px-6">
    {#each groups as g (g.title)}
      <section class="mb-4 last:mb-0" aria-label={g.title}>
        <h3 class="mb-1 text-xs font-semibold uppercase tracking-[0.14em] text-muted-foreground">{g.title}</h3>
        {#each g.list as a (a.id)}<AccountRow {a} {cash} {byName} {plaid} {mine} onhidden={moved} />{/each}
      </section>
    {:else}
      {#if !hiddenList.length}<p class="py-6 text-center text-sm text-muted-foreground">Accounts appear here after the first sync.</p>{/if}
    {/each}
    {#if hiddenList.length}
      <section class="mt-4 border-t pt-3 text-sm text-muted-foreground first:mt-0 first:border-t-0 first:pt-0" aria-label="Hidden accounts">
        <p>{plural(hiddenList.length, "hidden account")} ·
          <button type="button" class={linkCls} aria-expanded={hiddenOpen} onclick={() => (showHidden = !hiddenOpen)}>{hiddenOpen ? "Hide" : "Show"}</button></p>
        <p class="text-xs">Left out of lists, totals and the forecast; their transactions are kept.</p>
        {#if hiddenOpen}
          <div class="mt-2">{#each hiddenList as a (a.id)}<AccountRow {a} {cash} {byName} {plaid} {mine} onhidden={moved} />{/each}</div>
        {/if}
      </section>
    {/if}
    {#if deleted.length}
      <section class="mt-4 border-t pt-3 text-sm text-muted-foreground" aria-label="Deleted accounts">
        <p>{plural(deleted.length, "deleted account")} ·
          <button type="button" class={linkCls} aria-expanded={showDeleted} onclick={() => (showDeleted = !showDeleted)}>{showDeleted ? "Hide" : "Show"}</button></p>
        {#if showDeleted}
          <ul class="mt-2 flex flex-col gap-1.5"
            title="Restoring one lets the next sync bring it back, with whatever history the bank still offers (and a card linked to Plaid linked again); what was deleted with it doesn’t come back.">
            {#each deleted as d (d.id)}
              <li class="flex items-center gap-3"><span class="min-w-0 flex-1 truncate text-foreground">{d.name || d.id}</span>
                <Button variant="outline" size="sm" disabled={!!restoring} onclick={() => restore(d)}>{restoring === d.id ? "Restoring…" : "Restore"}</Button></li>
            {/each}
          </ul>
        {/if}
      </section>
    {/if}
  </Card.Content>
</Card.Root>
