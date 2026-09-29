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

  // The investment accounts: untick one to leave it out of the page, and enter the funds of a SimpleFIN account
  // that only sends a balance. An account connected through both Plaid and SimpleFIN is listed once, as its Plaid one.
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
            <label class="inline-flex items-center gap-2" title={a.hidden_in_accounts ? "Hidden in Settings → Accounts; show it there to count it here" : undefined}>
              <input type="checkbox" class="size-4 accent-[var(--nw-1)]" checked={!a.hidden} disabled={!!a.hidden_in_accounts}
                onchange={(e) => toggle(a, e.currentTarget.checked)} />
              <span>{a.institution_name ?? ""} · {a.name || a.official_name || ""}{a.mask ? ` ••${a.mask}` : ""}</span>
              {#if a.hidden_in_accounts}<Badge variant="secondary">hidden in Settings</Badge>{/if}
            </label>
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
