<script lang="ts">
  import type { SimplefinSeen } from "$lib/components/settings/types";
  import * as Alert from "$lib/components/ui/alert";
  import { Button } from "$lib/components/ui/button";
  import ChevronRight from "@lucide/svelte/icons/chevron-right";
  import TriangleAlert from "@lucide/svelte/icons/triangle-alert";
  import { pctAbs } from "./numbers";
  import TrackedEditor from "./TrackedEditor.svelte";
  import type { InvAccount } from "./types";

  // The SimpleFIN accounts that send only a balance, or whose funds you keep by hand: folded away at the bottom of
  // the page, a line each, with the closed summary saying when one's entered funds are off its balance.
  let { accounts, seen, onchanged }: { accounts: InvAccount[]; seen: SimplefinSeen[]; onchanged: () => void } = $props();

  const seenBy = $derived(Object.fromEntries(seen.map((x) => [x.id || "", x])));
  const balanceOnly = (a: InvAccount) => { const s = seenBy[a.id.slice(3)]; return !!s && !s.positions; };
  const lines = $derived(accounts.filter((a) => !a.hidden && a.source === "simplefin" && (a.tracked || balanceOnly(a))));
  let editing = $state<string | null>(null);
  const drifting = $derived(lines.filter((a) => a.tracked && a.drift != null && a.drift > 0.02).length);
  const summary = $derived(`${lines.length} account${lines.length === 1 ? "" : "s"}${drifting ? ` · ${drifting} to update` : ""}`);
</script>

{#if lines.length}
  <details class="group mb-6 rounded-xl border bg-card" data-testid="hand-tracked">
    <summary class="flex min-h-11 cursor-pointer list-none items-center gap-3 rounded-xl px-4 py-2 select-none [&::-webkit-details-marker]:hidden">
      <span class="text-sm font-medium">Accounts without automatic holdings</span>
      <span class={drifting ? "min-w-0 flex-1 truncate text-xs text-warning" : "min-w-0 flex-1 truncate text-xs text-muted-foreground"}
        data-testid="hand-tracked-summary">{summary}</span>
      <ChevronRight class="size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-90" aria-hidden="true" />
    </summary>
  <ul class="border-t px-4 py-1 text-sm">
    {#each lines as a (a.id)}
      <li class="border-t border-border py-2 first:border-t-0">
        <span class="text-muted-foreground">{a.name || a.official_name || "Account"} · {a.tracked ? "entered by hand" : "balance only"} ·</span>
        <Button variant="link" size="sm" class="h-auto px-0" aria-expanded={editing === a.id}
          onclick={() => (editing = editing === a.id ? null : a.id)}>{a.tracked ? "Edit holdings" : "Enter holdings"}</Button>
        {#if a.tracked && a.drift != null && a.drift > 0.02}
          <Alert.Root class="mt-2"><TriangleAlert /><Alert.Description>
            {a.name}: the funds you entered are {pctAbs(a.drift)} off the synced balance. Update the share counts from your latest statement.
          </Alert.Description></Alert.Root>
        {/if}
        {#if editing === a.id}
          <div class="mt-2"><TrackedEditor acctId={a.id} onclose={(changed) => { editing = null; if (changed) onchanged(); }} /></div>
        {/if}
      </li>
    {/each}
  </ul>
  </details>
{/if}
