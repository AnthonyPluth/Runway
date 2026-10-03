<script lang="ts">
  import { app } from "$lib/app.svelte";
  import { fmtDateTime, serverTime } from "$lib/format";
  import ProblemNote from "./ProblemNote.svelte";
  import ServiceRow from "./ServiceRow.svelte";
  import SimpleFinSetup from "./SimpleFinSetup.svelte";
  import type { SettingsAccount } from "./types";
  import { helpCls, linkCls } from "./ui";

  // Settings → Connections → SimpleFIN: the setup token when it isn't connected; once it is, when it last synced, which
  // banks it brings in (their accounts are matched to yours under Accounts) and a way to replace the token. A failed
  // sync, or one a bank still needs you for (an expired login, say), turns it to the warning tone and opens it: a failed
  // sync says so plainly, with SimpleFIN's own words under Details; a bank's message is shown as it is.
  let { accounts = [] }: { accounts?: SettingsAccount[] } = $props();

  const st = $derived(app.state!);
  const log = $derived(st.last_log);
  const warnings = $derived(st.sync_warnings ?? []);
  const failed = $derived(!!log && !log.ok);
  const problem = $derived(failed || warnings.length > 0);
  // When it ran, in this browser's time zone; the bank messages the log line also carries are shown on their own line.
  const when = $derived(log?.at ? fmtDateTime(serverTime(log.at)) : "");
  const summary = $derived(log?.ok ? (log.message ?? "").split(" · bank messages:")[0] : "");
  // Its accounts are the ones it syncs: not Plaid's own ("pl:…"), nor one switched to Plaid under Accounts (which keeps
  // its id). Hidden ones still sync, so they count. Its banks are their institutions.
  const mine = $derived(accounts.filter((a) => !a.id.startsWith("pl:") && a.provider !== "plaid"));
  const banks = $derived([...new Set(mine.map((a) => a.org?.trim()).filter(Boolean))].sort((a, b) => a!.localeCompare(b!)));
  let replacing = $state(false);
</script>

<!-- Until a bank is connected, one of the two ways to add one: collapsed beside Plaid, and the one to start with. -->
<ServiceRow name="SimpleFIN" purpose="Easiest · about $15 a year" on={!!st.simplefin} group="banks"
  tag={st.connected ? undefined : "Recommended"}
  status={st.simplefin ? (problem ? "Needs attention" : "Connected") : undefined} warn={!!st.simplefin && problem}>
  <p class={helpCls}>Banks are added and removed at <a class={linkCls} href="https://beta-bridge.simplefin.org" target="_blank" rel="noopener">SimpleFIN
    Bridge</a>.</p>
  {#if st.simplefin}
    <p class="text-sm text-muted-foreground">{log ? `Last sync ${when}${summary ? ` · ${summary}` : ""}` : "Not synced yet"}</p>
    {#if failed}<ProblemNote text="The last sync didn’t finish. Runway tries again with the next one." detail={log?.message} />{/if}
    {#each warnings as w (w)}<p class="text-sm text-warning">{w}</p>{/each}
    {#if mine.length}
      <p class="text-sm text-muted-foreground">{mine.length === 1 ? "1 account" : `${mine.length} accounts`}{#if banks.length}{" from "}{banks.join(", ")}{/if}{" · "}<a
        class={linkCls} href="#setup/accounts">Manage in Accounts</a></p>
    {/if}
    <div>
      <button type="button" class="text-sm text-muted-foreground underline underline-offset-4 hover:text-foreground" aria-expanded={replacing}
        onclick={() => (replacing = !replacing)}>Replace the setup token</button>
    </div>
    {#if replacing}<SimpleFinSetup ondone={() => (replacing = false)} />{/if}
    <!-- No Disconnect: the server has no way to forget the access URL yet; banks are removed at SimpleFIN Bridge. -->
  {:else}
    <p class={helpCls}>Paste a setup token from SimpleFIN Bridge.</p>
    <SimpleFinSetup />
  {/if}
</ServiceRow>
