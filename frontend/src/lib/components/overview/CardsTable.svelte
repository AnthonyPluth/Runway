<script lang="ts">
  import { isPhone } from "$lib/phone.svelte";
  import { api } from "$lib/api";
  import { reload } from "$lib/app.svelte";
  import AcctLabel from "$lib/components/AcctLabel.svelte";
  import AmountEdit from "$lib/components/AmountEdit.svelte";
  import { Badge } from "$lib/components/ui/badge";
  import { Button } from "$lib/components/ui/button";
  import { fmt, fmtDate, isoDay, nb, parseDate } from "$lib/format";
  import type { CardSummary } from "$lib/types";
  import { toast } from "svelte-sonner";

  // Each card's latest statement (click it to correct the bank's figure), when it's due and its usual spending, and, for
  // a card that isn't paid in full, how much of it the forecast pays.
  let { cards }: { cards: CardSummary[] } = $props();
  const today = parseDate(isoDay());

  async function setStatement(c: CardSummary, value: number) {
    try { await api("/api/overrides", { method: "POST", body: { key: c.statement_key, amount: value } }); toast.success("Statement balance saved"); reload(); }
    catch (err) { toast.error((err as Error).message); }
  }
  async function reset(c: CardSummary) {
    try { await api("/api/overrides", { method: "DELETE", body: { key: c.statement_key } }); toast.success("Back to the calculated amount"); reload(); }
    catch (err) { toast.error((err as Error).message); }
  }
</script>

<!-- One row per card, for a grouped list. -->
{#if !cards.length}
  <p class="cell text-sm text-muted-foreground">Enter each card’s latest statement in {#if isPhone()}Settings → Accounts on a computer{:else}<a class="font-medium text-foreground underline underline-offset-4" href="#setup/accounts">Settings → Accounts</a>{/if} (or link it through Plaid) to see what’s due and when.</p>
{:else}
  {#each cards as c (c.id)}
    {@const soon = (c.payment ?? c.remaining) > 0 && (parseDate(c.due_date).getTime() - today.getTime()) / 864e5 <= 7}
    <div class="cell items-start">
      <div class="min-w-0 flex-1">
        <div class="text-[15px]"><AcctLabel id={c.id} name={c.name} /></div>
        <div class="mt-0.5 text-[13px] text-muted-foreground tabular-nums">
          owes {fmt(c.owed_now)} now ·{#if c.statement_source === "manual"}
            <span title="You entered this statement in Settings → Accounts">entered by hand</span> ·{/if}
          <span title={c.avg_cycles ? `Average per statement over the last ${c.avg_cycles} statement${c.avg_cycles === 1 ? "" : "s"}; used to forecast future payments` : "Not enough history yet; using recent daily spending"}>
            {c.avg_monthly_spend != null ? `about ${fmt(c.avg_monthly_spend)} a statement` : "no average yet"}</span>
        </div>
      </div>
      <div class="flex shrink-0 flex-col items-end text-right tabular-nums">
        <span class="flex items-center gap-1.5 text-[15px]">
          {#if c.statement_set}<Badge variant="secondary" title={`Entered by you · the bank reported ${fmt(c.statement_reported)}`}>set</Badge>{/if}
          <AmountEdit amount={c.statement_balance} label="Statement balance" title={`Closed ${fmtDate(c.last_close)} · click to correct it`}
            save={(v) => setStatement(c, v)} />
        </span>
        <span class="text-[13px] text-muted-foreground">
          {#if c.remaining > 0}
            <span class={soon ? "font-medium text-amber-400" : ""}>due {fmtDate(c.due_date)}</span>
            {#if c.pay_mode && c.pay_mode !== "full"}
              · <span title={(c.carried ?? 0) < 0 ? `The extra ${fmt(-(c.carried ?? 0))} comes off the next statement` : `The rest, ${fmt(c.carried ?? 0)}, carries into the next statement`}>{nb(`pays ${fmt(c.payment ?? 0)} of ${fmt(c.remaining)}`)}</span>
            {:else}{c.remaining < c.statement_balance - 0.005 ? ` · ${fmt(c.remaining)} left` : ""}{/if}{#if c.minimum_payment} · {nb(`min ${fmt(c.minimum_payment)}`)}{/if}
          {:else}<span class="text-emerald-400">Paid ✓</span>{/if}
          {#if c.statement_set} · <Button variant="link" size="sm" class="h-auto p-0 text-xs" title={`Go back to the bank's figure (${fmt(c.statement_reported)})`} onclick={() => reset(c)}>reset</Button>{/if}
        </span>
      </div>
    </div>
  {/each}
{/if}
