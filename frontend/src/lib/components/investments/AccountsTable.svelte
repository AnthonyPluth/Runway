<script lang="ts">
  import { api } from "$lib/api";
  import * as Alert from "$lib/components/ui/alert";
  import { Badge } from "$lib/components/ui/badge";
  import { Button } from "$lib/components/ui/button";
  import { fmt } from "$lib/format";
  import TriangleAlert from "@lucide/svelte/icons/triangle-alert";
  import { toast } from "svelte-sonner";
  import TrackedEditor from "./TrackedEditor.svelte";
  import type { InvAccount, SimplefinSeen } from "./types";

  // The investment accounts, and where to enter the funds of a SimpleFIN account that only sends a balance. Accounts
  // aren't left out here any more (leave one out of net worth on that page); one you had hidden can be shown again. An account connected through both Plaid and SimpleFIN is listed once, as its Plaid one.
  let { accounts, seen, onchanged }: { accounts: InvAccount[]; seen: SimplefinSeen[]; onchanged: () => void } = $props();

  const seenBy = $derived(Object.fromEntries(seen.map((x) => [x.id || "", x])));
  const sfSeen = (a: InvAccount) => seenBy[a.id.slice(3)];
  const via = (a: InvAccount) => {
    if (a.source !== "simplefin") return `via Plaid${a.subtype ? ` · ${a.subtype}` : ""}${a.also_simplefin ? " · also in SimpleFIN" : ""}`;
    const s = sfSeen(a);
    return `via SimpleFIN${s ? ` · ${s.positions ? `${s.positions} positions` : "balance only"}` : ""}`;
  };
  const canTrack = (a: InvAccount) => a.source === "simplefin" && (a.tracked || (sfSeen(a) && !sfSeen(a).positions));

  async function toggle(a: InvAccount, on: boolean) {
    try { await api(`/api/plaid/accounts/${encodeURIComponent(a.id)}`, { method: "POST", body: { hidden: !on } }); onchanged(); }
    catch (err) { toast.error((err as Error).message); }
  }
  let editing = $state<string | null>(null);
</script>

<div class="overflow-x-auto">
  <table class="w-full text-sm">
    <tbody>
      {#each accounts as a (a.id)}
        <tr class="border-t border-border first:border-t-0 [&>td]:py-2">
          <td class="pr-3">
            <span class="inline-flex flex-wrap items-center gap-2">
              <span>{a.institution_name ?? ""} · {a.name || a.official_name || ""}{a.mask ? ` ••${a.mask}` : ""}</span>
              {#if a.hidden_in_accounts}<Badge variant="secondary" title="Hidden in Settings → Accounts; show it there to count it here">hidden in Settings</Badge>
              {:else if a.duplicate_of}<Badge variant="secondary" title="The same account, connected another way, is shown instead">also connected another way</Badge>
              {:else if a.hidden}
                <Badge variant="secondary">hidden</Badge>
                <Button variant="link" size="sm" class="h-auto px-0" aria-label={`Show ${a.name || a.official_name || "this account"} again`} onclick={() => toggle(a, true)}>Show again</Button>
              {/if}
            </span>
          </td>
          <td class="pr-3 text-xs text-muted-foreground">{via(a)}</td>
          <td class="pr-3 text-right tabular-nums">{fmt(a.balance)}</td>
          <td class="text-right whitespace-nowrap">
            {#if canTrack(a)}
              <Button variant="link" size="sm" class="px-0" aria-expanded={editing === a.id}
                onclick={() => (editing = editing === a.id ? null : a.id)}>{a.tracked ? "Edit holdings" : "Enter holdings"}</Button>
            {/if}
          </td>
        </tr>
        {#if a.tracked && a.drift != null && a.drift > 0.02}
          <tr><td colspan="4" class="pb-2">
            <Alert.Root><TriangleAlert /><Alert.Description>
              {a.name}: the funds you entered are {(a.drift * 100).toFixed(1)}% off the synced balance. Update the share counts from your latest statement.
            </Alert.Description></Alert.Root>
          </td></tr>
        {/if}
        {#if editing === a.id}
          <tr><td colspan="4" class="pb-3">
            <TrackedEditor acctId={a.id} onclose={(changed) => { editing = null; if (changed) onchanged(); }} />
          </td></tr>
        {/if}
      {/each}
    </tbody>
  </table>
</div>
