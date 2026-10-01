<script lang="ts">
  import { api } from "$lib/api";
  import { reload } from "$lib/app.svelte";
  import { autosave } from "$lib/autosave";
  import MissedAlert from "$lib/components/MissedAlert.svelte";
  import { Badge } from "$lib/components/ui/badge";
  import { Button } from "$lib/components/ui/button";
  import { ConfirmDialog } from "$lib/components/ui/confirm-dialog";
  import { fmt, fmtDate, nb, plural } from "$lib/format";
  import type { Account } from "$lib/types";
  import { cn } from "$lib/utils";
  import { toast } from "svelte-sonner";
  import { tick, untrack } from "svelte";
  import ChevronRight from "@lucide/svelte/icons/chevron-right";
  import CatIcon from "$lib/components/CatIcon.svelte";
  import Logo from "$lib/components/Logo.svelte";
  import LogoPicker from "$lib/components/transactions/LogoPicker.svelte";
  import RecIcon from "./RecIcon.svelte";
  import RecurringFields from "./RecurringFields.svelte";
  import { FREQ, validate, type MatchedTx, type RecurringItem, type RecurringValues } from "./types";

  // One recurring item: a summary line that opens into its fields, each saved as you change it. After a save,
  // `onsaved` gets the fresh list, so the summary (amount, next date, matches) and its Money in/out group catch up.
  let { r, accounts, open, ontoggle, onsaved }: { r: RecurringItem; accounts: Account[]; open: boolean; ontoggle: (open: boolean) => void; onsaved?: (items: RecurringItem[]) => void } = $props();

  const init = () => ({
    name: r.name, account_id: r.account_id, amount: r.amount, amount_mode: r.amount_mode || "fixed", frequency: r.frequency,
    dates: r.dates || "", anchor_date: r.anchor_date || "", match: r.match || "", amount_min: r.amount_min ?? "", amount_max: r.amount_max ?? "",
  });
  let v: RecurringValues = $state(init());
  // Open or closed is yours to change; `open` only says how it starts.
  let isOpen = $state(untrack(() => open));
  let active = $state(untrack(() => !!r.active));
  let dismissed = $state<string[]>([]);
  const missed = $derived((r.missed ?? []).filter((m) => !dismissed.includes(m.key)));
  let attempted = $state(false);   // once a save was refused, the fields that need fixing say so
  const errors = $derived(attempted ? validate(v) : {});
  let matches = $state<MatchedTx[] | null>(null);
  // How each matched transaction got here; nothing for links from before Runway kept it.
  const LINKED_BY: Record<string, string> = { you: "linked by you", auto: "matched automatically" };

  const amt = $derived(r.expected_amount ?? r.amount);
  const sub = $derived([FREQ[v.frequency] || v.frequency, r.next_date ? `next ${fmtDate(r.next_date)}` : "no upcoming date",
    r.matched_count ? `${r.matched_count} matched` : ""].filter(Boolean));

  // The logo picker chooses by the item's name (the one the logo comes from, else its last matched transaction's), the
  // same choice Transactions keeps by merchant name. A new one shows here and in Upcoming, so reload both.
  async function logoChanged() {
    reload();
    try { onsaved?.(await api<RecurringItem[]>("/api/recurring")); }
    catch { /* chosen; the list catches up on the next load */ }
  }

  type Field = HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement;
  async function save(f: Field) {
    await tick();   // let the field's binding catch up with the change first
    // Nothing saves until the fields are complete. Throwing (rather than returning) keeps "Saved ✓" from flashing.
    const errs = validate(v);
    const first = Object.values(errs)[0];
    attempted = !!first;
    if (first) {
      if (errs.dates && f.name === "frequency") f.closest("details")?.querySelector<HTMLInputElement>("input[name=dates]")?.focus();
      throw new Error(`Not saved yet. ${first}`);
    }
    const res = await api<{ linked: number; amount_min?: number | null; amount_max?: number | null }>(`/api/recurring/${r.id}`, { method: "POST", body: { ...v, active: active ? 1 : 0 } });
    if (res.linked) toast(`Saved · matched ${res.linked} more`);
    // A new amount outside the range moves the range with it (the server says where to), so the fields show that.
    if (res.amount_min !== undefined) { v.amount_min = res.amount_min ?? ""; v.amount_max = res.amount_max ?? ""; }
    if (onsaved) {
      try { onsaved(await api<RecurringItem[]>("/api/recurring")); }
      catch { /* saved; the summary catches up on the next load */ }
    }
  }
  // Removing says what it does first: the matched transactions are unlinked (they stay in your history) and its one-off
  // changes to single dates go.
  let removing = $state(false);
  async function remove(): Promise<boolean> {
    try { await api(`/api/recurring/${r.id}`, { method: "DELETE" }); toast("Removed"); reload(); return true; }
    catch (err) { toast.error((err as Error).message); return false; }
  }
  async function showMatches() {
    if (matches) { matches = null; return; }
    try { matches = (await api<{ items: MatchedTx[] }>(`/api/transactions?recurring=${r.id}&limit=50`)).items; }
    catch (err) { toast.error((err as Error).message); }
  }
</script>

<details class="group border-t first:border-t-0" bind:open={isOpen} ontoggle={(e) => ontoggle(e.currentTarget.open)}>
  <summary class="-mx-2 flex cursor-pointer list-none items-center gap-3 rounded-lg px-2 py-3 hover:bg-muted/50 group-open:bg-muted/40 [&::-webkit-details-marker]:hidden">
    <!-- A button inside the summary doesn't toggle it. -->
    <LogoPicker name={r.name} onchanged={logoChanged}>
      {#if r.logo}<Logo src={r.logo} size={28} />
      {:else if r.last_matched?.category}<CatIcon name={r.last_matched.category} size={28} class="rounded-full" />
      {:else}<RecIcon id={r.account_id} />{/if}
    </LogoPicker>
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
    {#if missed.length}<div class="group-list mb-3 bg-muted/60" style:--inset="3.75rem">{#each missed as m (m.key)}<MissedAlert {m} ondismiss={(k) => (dismissed = [...dismissed, k])} />{/each}</div>{/if}
    <RecurringFields bind:v {accounts} {save} {errors} suggested={r.suggested_amount} suggestedFor={r.amount} />
    <div class="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2">
      <label class="relative flex cursor-pointer items-center gap-2 text-sm">
        <input type="checkbox" name="active" class="size-4 accent-primary" bind:checked={active} use:autosave={save} /> Active
      </label>
      {#if r.matched_count}
        <Button variant="link" size="sm" class="h-auto px-0" aria-expanded={!!matches} onclick={showMatches}>
          {matches ? "Hide matched transactions" : "Show matched transactions"}
        </Button>
      {/if}
      <Button variant="link" size="sm" class="ml-auto h-auto px-0" onclick={() => (removing = true)}>Remove</Button>
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
                  <td class="px-2 text-xs whitespace-nowrap text-muted-foreground">{LINKED_BY[t.recurring_linked_by ?? ""] ?? ""}</td>
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

<ConfirmDialog bind:open={removing} destructive title={`Remove ${r.name}?`} confirmLabel="Remove" busyLabel="Removing…" onconfirm={remove}
  description={`${r.matched_count ? `This unlinks ${plural(r.matched_count, "matched transaction")}; they stay in your history.` : "No transactions are linked to it."} Any one-off changes you made to its dates are cleared too.`} />
