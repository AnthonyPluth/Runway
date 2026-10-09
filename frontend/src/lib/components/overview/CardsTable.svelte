<script lang="ts">
  import { api } from "$lib/api";
  import AcctLabel from "$lib/components/AcctLabel.svelte";
  import AmountEdit from "$lib/components/AmountEdit.svelte";
  import { Badge } from "$lib/components/ui/badge";
  import { Button } from "$lib/components/ui/button";
  import { fmt, fmtDate, isoDay, nb, parseDate } from "$lib/format";
  import type { CardSummary } from "$lib/types";
  import { toast } from "svelte-sonner";
  import { act } from "$lib/act";

  // What each card still has due on its latest statement (click it to correct it), when it's due, and, for a card
  // that isn't paid in full, how much of it the forecast pays. The statement itself is only in the amount's tooltip.
  // `onchanged` loads the forecast again after an amount due is corrected, in place.
  let { cards, onchanged }: { cards: CardSummary[]; onchanged: () => void } = $props();
  // A card that owes nothing (owed_now is never below zero: a credit reads as $0), with nothing left to pay on its
  // statement, has nothing to say here. The rest in the order they're due: the soonest (or overdue) first, then the
  // ones already paid, by name.
  const shown = $derived(cards.filter((c) => c.owed_now >= 0.005 || c.remaining > 0).toSorted((a, b) =>
    (a.remaining > 0 ? 0 : 1) - (b.remaining > 0 ? 0 : 1) || (a.remaining > 0 ? a.due_date.localeCompare(b.due_date) : 0) || a.name.localeCompare(b.name)));
  const today = parseDate(isoDay());

  // What's come off the statement since it closed (payments and posted credits). The amount due is the statement less
  // this, so an amount due you enter is kept as the statement it implies (the override on statement_key): it keeps
  // coming down as more is paid, and reset goes back to exactly the bank's statement.
  const cents = (n: number) => Math.round(n * 100) / 100;
  const offSince = (c: CardSummary) => (c.paid_since_close ?? 0) + (c.credits_since_close ?? 0);
  const bankDue = (c: CardSummary) => Math.max(0, cents((c.statement_reported ?? c.statement_balance) - offSince(c)));
  function dueTitle(c: CardSummary) {
    const paid = c.paid_since_close ?? 0, credits = c.credits_since_close ?? 0;
    const since = [paid > 0 && `paid ${fmt(paid)}`, credits > 0 && `credited ${fmt(credits)}`].filter(Boolean).join(" and ");
    return `Amount due · statement closed ${fmtDate(c.last_close)}: ${fmt(c.statement_balance)}${since ? ` · ${since} since` : ""} · click to correct it`;
  }

  async function setDue(c: CardSummary, value: number) {
    await act(async () => { await api("/api/overrides", { method: "POST", body: { key: c.statement_key, amount: cents(value + offSince(c)) } }); toast.success("Amount due saved"); onchanged(); });
  }
  async function reset(c: CardSummary) {
    await act(async () => { await api("/api/overrides", { method: "DELETE", body: { key: c.statement_key } }); toast.success("Back to the calculated amount"); onchanged(); });
  }
</script>

<!-- One row per card, for a grouped list. -->
{#if !cards.length}
  <p class="cell text-sm text-muted-foreground">Enter each card’s latest statement in <a class="font-medium text-foreground underline underline-offset-4" href="#setup/accounts">Settings → Accounts</a> (or link it through Plaid) to see what’s due and when.</p>
{:else if !shown.length}
  <p class="cell text-sm text-muted-foreground">None of your cards owe anything right now.</p>
{:else}
  {#each shown as c (c.id)}
    {@const soon = (c.payment ?? c.remaining) > 0 && (parseDate(c.due_date).getTime() - today.getTime()) / 864e5 <= 7}
    <div class="cell items-start">
      <div class="min-w-0 flex-1">
        <div class="text-[15px]"><AcctLabel id={c.id} name={c.name} /></div>
        <div class="mt-0.5 text-[13px] text-muted-foreground tabular-nums">
          balance {fmt(c.owed_now)}{#if c.statement_source === "manual"}{" · "}<span title="You entered this statement in Settings → Accounts">entered by hand</span>{/if}
        </div>
      </div>
      <div class="flex shrink-0 flex-col items-end text-right tabular-nums">
        <span class="flex items-center gap-1.5 text-[15px]">
          {#if c.statement_set}<Badge variant="secondary" title={`Entered by you · the bank’s statement leaves ${fmt(bankDue(c))} due`}>set</Badge>{/if}
          <AmountEdit amount={c.remaining} label="Amount due" title={dueTitle(c)} save={(v) => setDue(c, v)} />
        </span>
        <span class="text-[13px] text-muted-foreground">
          {#if c.remaining > 0}
            <span class={soon ? "font-medium text-warning" : ""}>due {fmtDate(c.due_date)}</span>
            {#if c.pay_mode && c.pay_mode !== "full"}
              · <span title={(c.carried ?? 0) < 0 ? `The extra ${fmt(-(c.carried ?? 0))} comes off the next statement` : `The rest, ${fmt(c.carried ?? 0)}, carries into the next statement`}>{nb(`pays ${fmt(c.payment ?? 0)} of ${fmt(c.remaining)}`)}</span>
            {/if}{#if c.minimum_payment} · {nb(`min ${fmt(c.minimum_payment)}`)}{/if}
          {:else}<span class="text-good">Paid ✓</span>{/if}
          {#if c.statement_set} · <Button variant="link" size="sm" class="h-auto p-0 text-xs" title={`Go back to the amount due on the bank's statement (${fmt(bankDue(c))})`} onclick={() => reset(c)}>reset</Button>{/if}
        </span>
      </div>
    </div>
  {/each}
{/if}
