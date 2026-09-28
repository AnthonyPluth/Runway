<script lang="ts">
  import { api } from "$lib/api";
  import { reload } from "$lib/app.svelte";
  import { autosave } from "$lib/autosave";
  import ConfirmButton from "$lib/components/ConfirmButton.svelte";
  import MissedAlert from "$lib/components/MissedAlert.svelte";
  import { Badge } from "$lib/components/ui/badge";
  import { Button } from "$lib/components/ui/button";
  import { fmt, fmtDate, nb } from "$lib/format";
  import type { Account } from "$lib/types";
  import { cn } from "$lib/utils";
  import { toast } from "svelte-sonner";
  import { tick, untrack } from "svelte";
  import ChevronRight from "@lucide/svelte/icons/chevron-right";
  import CatIcon from "$lib/components/CatIcon.svelte";
  import RecIcon from "./RecIcon.svelte";
  import RecurringFields from "./RecurringFields.svelte";
  import { FREQ, needsDates, type MatchedTx, type RecurringItem, type RecurringValues } from "./types";

  // One recurring item: a summary line that opens into its fields, each saved as you change it.
  let { r, accounts, open, ontoggle }: { r: RecurringItem; accounts: Account[]; open: boolean; ontoggle: (open: boolean) => void } = $props();

  const init = () => ({
    name: r.name, account_id: r.account_id, amount: r.amount, amount_mode: r.amount_mode || "fixed", frequency: r.frequency,
    dates: r.dates || "", anchor_date: r.anchor_date || "", match: r.match || "",
  });
  let v: RecurringValues = $state(init());
  // Open or closed is yours to change; `open` only says how it starts.
  let isOpen = $state(untrack(() => open));
  let active = $state(untrack(() => !!r.active));
  let missed = $state(untrack(() => r.missed ?? []));
  let matches = $state<MatchedTx[] | null>(null);

  const amt = $derived(r.expected_amount ?? r.amount);
  const sub = $derived([FREQ[v.frequency] || v.frequency, r.next_date ? `next ${fmtDate(r.next_date)}` : "no upcoming date",
    r.matched_count ? `${r.matched_count} matched` : ""].filter(Boolean));

  type Field = HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement;
  async function save(f: Field) {
    await tick();   // let the field's binding catch up with the change first
    // Don't save a dates schedule until its dates are filled in.
    if (needsDates(v.frequency) && !v.dates.trim()) {
      if (f.name === "frequency") f.closest("details")?.querySelector<HTMLInputElement>("input[name=dates]")?.focus();
      return;
    }
    const res = await api<{ linked: number }>(`/api/recurring/${r.id}`, { method: "POST", body: { ...v, active: active ? 1 : 0 } });
    if (res.linked) toast(`Saved · matched ${res.linked} more`);
  }
  async function remove() {
    try { await api(`/api/recurring/${r.id}`, { method: "DELETE" }); toast("Removed"); reload(); }
    catch (err) { toast.error((err as Error).message); }
  }
  async function showMatches() {
    if (matches) { matches = null; return; }
    try { matches = (await api<{ items: MatchedTx[] }>(`/api/transactions?recurring=${r.id}&limit=50`)).items; }
    catch (err) { toast.error((err as Error).message); }
  }
</script>

<details class="group border-t first:border-t-0" bind:open={isOpen} ontoggle={(e) => ontoggle(e.currentTarget.open)}>
  <summary class="-mx-2 flex cursor-pointer list-none items-center gap-3 rounded-lg px-2 py-3 hover:bg-muted/50 group-open:bg-muted/40 [&::-webkit-details-marker]:hidden">
    {#if r.last_matched?.category}<CatIcon name={r.last_matched.category} size={28} class="rounded-full" />{:else}<RecIcon id={r.account_id} />{/if}
    <span class="flex min-w-0 flex-1 flex-col gap-0.5">
      <span class="flex min-w-0 items-center gap-2 font-medium"><span class="truncate">{v.name || r.name}</span>{#if !active}<Badge variant="secondary">paused</Badge>{/if}</span>
      <span class="text-xs text-muted-foreground">
        {sub.map(nb).join(" · ")}{#if missed.length}{" · "}<span class="text-(--low)">{nb("missed a payment")}</span>{/if}
      </span>
    </span>
    <span class={cn("shrink-0 font-medium tabular-nums", amt > 0 && "text-emerald-500")}>{amt > 0 ? "+" : "−"}{fmt(Math.abs(amt))}</span>
    <ChevronRight class="size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-90" aria-hidden="true" />
  </summary>
  <div class="pt-3 pb-5 sm:pl-10">
    {#each missed as m (m.key)}<MissedAlert {m} ondismiss={(k) => (missed = missed.filter((x) => x.key !== k))} />{/each}
    <RecurringFields bind:v {accounts} {save} />
    <div class="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2">
      <label class="relative flex cursor-pointer items-center gap-2 text-sm">
        <input type="checkbox" name="active" class="size-4 accent-primary" bind:checked={active} use:autosave={save} /> Active
      </label>
      {#if r.matched_count}
        <Button variant="link" size="sm" class="h-auto px-0" aria-expanded={!!matches} onclick={showMatches}>
          {matches ? "Hide matched transactions" : "Show matched transactions"}
        </Button>
      {/if}
      <ConfirmButton class="ml-auto h-auto px-0" confirm="Remove?" onconfirm={remove}>Remove</ConfirmButton>
    </div>
    {#if matches}
      {#if matches.length}
        <div class="mt-3 overflow-x-auto">
          <table class="w-full text-sm">
            <tbody>
              {#each matches as t (t.id)}
                <tr class="border-t [&>td]:py-1.5">
                  <td class="w-20 whitespace-nowrap text-muted-foreground">{fmtDate(t.posted)}</td>
                  <td class="px-2">{t.description}</td>
                  <td class="text-right whitespace-nowrap tabular-nums">{fmt(t.amount)}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {:else}
        <p class="mt-3 text-sm text-muted-foreground">No matched transactions.</p>
      {/if}
    {/if}
  </div>
</details>
