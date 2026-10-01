<script lang="ts">
  import type { SimplefinSeen } from "$lib/components/settings/types";
  import * as Alert from "$lib/components/ui/alert";
  import { Button } from "$lib/components/ui/button";
  import TriangleAlert from "@lucide/svelte/icons/triangle-alert";
  import TrackedEditor from "./TrackedEditor.svelte";
  import type { InvAccount } from "./types";

  // One quiet line for each SimpleFIN account that sends only a balance, or whose funds you keep by hand.
  let { accounts, seen, onchanged }: { accounts: InvAccount[]; seen: SimplefinSeen[]; onchanged: () => void } = $props();

  const seenBy = $derived(Object.fromEntries(seen.map((x) => [x.id || "", x])));
  const balanceOnly = (a: InvAccount) => { const s = seenBy[a.id.slice(3)]; return !!s && !s.positions; };
  const lines = $derived(accounts.filter((a) => !a.hidden && a.source === "simplefin" && (a.tracked || balanceOnly(a))));
  let editing = $state<string | null>(null);
</script>

{#if lines.length}
  <ul class="mt-4 text-sm" data-testid="hand-tracked">
    {#each lines as a (a.id)}
      <li class="border-t border-border py-2 first:border-t-0">
        <span class="text-muted-foreground">{a.name || a.official_name || "Account"} · {a.tracked ? "entered by hand" : "balance only"} ·</span>
        <Button variant="link" size="sm" class="h-auto px-0" aria-expanded={editing === a.id}
          onclick={() => (editing = editing === a.id ? null : a.id)}>{a.tracked ? "Edit holdings" : "Enter holdings"}</Button>
        {#if a.tracked && a.drift != null && a.drift > 0.02}
          <Alert.Root class="mt-2"><TriangleAlert /><Alert.Description>
            {a.name}: the funds you entered are {(a.drift * 100).toFixed(1)}% off the synced balance. Update the share counts from your latest statement.
          </Alert.Description></Alert.Root>
        {/if}
        {#if editing === a.id}
          <div class="mt-2"><TrackedEditor acctId={a.id} onclose={(changed) => { editing = null; if (changed) onchanged(); }} /></div>
        {/if}
      </li>
    {/each}
  </ul>
{/if}
