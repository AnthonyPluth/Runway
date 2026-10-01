<script lang="ts">
  import { app } from "$lib/app.svelte";
  import { Badge } from "$lib/components/ui/badge";
  import * as Card from "$lib/components/ui/card";
  import { fmtDateTime, serverTime } from "$lib/format";
  import SimpleFinSetup from "./SimpleFinSetup.svelte";
  import type { SettingsAccount } from "./types";
  import { helpCls, linkCls } from "./ui";

  // Settings → Connections → SimpleFIN: the setup token when it isn't connected; once it is, when it last synced, which
  // banks it brings in (their accounts are matched to yours under Accounts) and a way to replace the token. A failed
  // sync, or one a bank still needs you for (an expired login, say), turns it amber with what was said.
  let { accounts = [] }: { accounts?: SettingsAccount[] } = $props();

  const st = $derived(app.state!);
  const log = $derived(st.last_log);
  const warnings = $derived(st.sync_warnings ?? []);
  const problem = $derived(log && !log.ok ? log.message || "The last sync failed." : warnings.join("; "));
  // When it ran, in this browser's time zone; the bank messages the log line also carries are shown on their own line.
  const when = $derived(log?.at ? fmtDateTime(serverTime(log.at)) : "");
  const summary = $derived(log?.ok ? (log.message ?? "").split(" · bank messages:")[0] : "");
  // Its accounts are the ones it syncs: not Plaid's own ("pl:…"), nor one switched to Plaid under Accounts (which keeps
  // its id). Hidden ones still sync, so they count. Its banks are their institutions.
  const mine = $derived(accounts.filter((a) => !a.id.startsWith("pl:") && a.provider !== "plaid"));
  const banks = $derived([...new Set(mine.map((a) => a.org?.trim()).filter(Boolean))].sort((a, b) => a!.localeCompare(b!)));
  let replacing = $state(false);
</script>

<Card.Root>
  <Card.Header>
    <Card.Title class="flex flex-wrap items-center gap-1.5">SimpleFIN
      {#if st.simplefin}
        {#if problem}<Badge variant="outline" class="border-amber-500/50 text-amber-500">needs attention</Badge>{:else}<Badge variant="secondary">connected</Badge>{/if}
      {/if}
    </Card.Title>
    <Card.Description>Your banks through <a class={linkCls} href="https://beta-bridge.simplefin.org" target="_blank" rel="noopener">SimpleFIN
      Bridge</a>, synced together once a day. Banks are added and removed there.</Card.Description>
  </Card.Header>
  <Card.Content class="flex flex-col gap-3">
    {#if st.simplefin}
      <p class="text-sm text-muted-foreground">{log ? `Last sync ${when}${summary ? ` · ${summary}` : ""}` : "Not synced yet"}</p>
      {#if problem}<p class="text-sm text-amber-500">{problem}</p>{/if}
      {#if mine.length}
        <p class="text-sm text-muted-foreground">{mine.length === 1 ? "1 account" : `${mine.length} accounts`}{#if banks.length}{" from "}{banks.join(", ")}{/if}{" · "}<a
          class={linkCls} href="#setup/accounts">Manage in Accounts</a></p>
      {/if}
      <div>
        <button type="button" class="text-sm text-muted-foreground underline underline-offset-4 hover:text-foreground" aria-expanded={replacing}
          onclick={() => (replacing = !replacing)}>Replace the setup token</button>
      </div>
      {#if replacing}<SimpleFinSetup ondone={() => (replacing = false)} />{/if}
    {:else}
      <p class={helpCls}>Paste a setup token from SimpleFIN Bridge; Runway claims it and runs the first sync (about six months of history).</p>
      <SimpleFinSetup />
    {/if}
  </Card.Content>
</Card.Root>
