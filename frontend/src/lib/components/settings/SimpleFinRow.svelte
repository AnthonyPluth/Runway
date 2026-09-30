<script lang="ts">
  import { app } from "$lib/app.svelte";
  import { Badge } from "$lib/components/ui/badge";
  import { serverTime } from "$lib/format";
  import { cn } from "$lib/utils";
  import BankIcon from "./BankIcon.svelte";
  import SimpleFinSetup from "./SimpleFinSetup.svelte";

  // The SimpleFIN connection as one compact row (shown once it's connected): when it last synced, and a way to replace it.
  // A failed sync, or one a bank still needs you for (an expired login, say), turns it amber with what was said.
  const st = $derived(app.state!);
  const log = $derived(st.last_log);
  const warnings = $derived(st.sync_warnings ?? []);
  const problem = $derived(log && !log.ok ? log.message || "The last sync failed." : warnings.join("; "));
  // When it ran, in this browser's time zone; the bank messages the log line also carries are shown on their own line.
  const when = $derived(log?.at ? serverTime(log.at).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "");
  const summary = $derived(log?.ok ? (log.message ?? "").split(" · bank messages:")[0] : "");
  let replacing = $state(false);
</script>

<div class={cn("rounded-xl border px-3 py-2.5", problem && "border-amber-500/50")}>
  <div class="flex flex-wrap items-center gap-3">
    <BankIcon name="SimpleFIN" />
    <span class="flex min-w-0 flex-1 flex-col">
      <span class="flex flex-wrap items-center gap-1.5 font-medium">SimpleFIN
        {#if problem}<Badge variant="outline" class="border-amber-500/50 text-amber-500">needs attention</Badge>{:else}<Badge variant="secondary">connected</Badge>{/if}
      </span>
      <span class="text-xs text-muted-foreground">
        {log ? `Last sync ${when}${summary ? ` · ${summary}` : ""}` : "Not synced yet"}
      </span>
      {#if problem}<span class="text-xs text-amber-500">{problem}</span>{/if}
    </span>
    <button type="button" class="text-sm text-muted-foreground underline underline-offset-4 hover:text-foreground" aria-expanded={replacing}
      onclick={() => (replacing = !replacing)}>Replace the connection</button>
  </div>
  {#if replacing}
    <div class="mt-3 sm:ml-10"><SimpleFinSetup ondone={() => (replacing = false)} /></div>
  {/if}
</div>
