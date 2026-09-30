<script lang="ts">
  import { app } from "$lib/app.svelte";
  import { Badge } from "$lib/components/ui/badge";
  import type { SyncLog } from "$lib/types";
  import BankIcon from "./BankIcon.svelte";
  import SimpleFinSetup from "./SimpleFinSetup.svelte";

  // The SimpleFIN connection as one compact row (shown once it's connected): when it last synced, and a way to replace it.
  const st = $derived(app.state!);
  // /api/state's last_log also has when it ran (`at`, UTC), which lib/types.ts doesn't list.
  const log = $derived(st.last_log as (SyncLog & { at?: string }) | null | undefined);
  let replacing = $state(false);
</script>

<div class="rounded-xl border px-3 py-2.5">
  <div class="flex flex-wrap items-center gap-3">
    <BankIcon name="SimpleFIN" />
    <span class="flex min-w-0 flex-1 flex-col">
      <span class="flex flex-wrap items-center gap-1.5 font-medium">SimpleFIN <Badge variant="secondary">connected</Badge></span>
      <span class="text-xs text-muted-foreground">{#if log}Last sync: {log.at} UTC — {log.message}{:else}not synced yet{/if}</span>
    </span>
    <button type="button" class="text-sm text-muted-foreground underline underline-offset-4 hover:text-foreground" aria-expanded={replacing}
      onclick={() => (replacing = !replacing)}>Replace the connection</button>
  </div>
  {#if replacing}
    <div class="mt-3 sm:ml-10"><SimpleFinSetup ondone={() => (replacing = false)} /></div>
  {/if}
</div>
