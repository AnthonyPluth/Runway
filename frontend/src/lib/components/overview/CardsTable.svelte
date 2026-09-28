<script lang="ts">
  import { api } from "$lib/api";
  import { reload } from "$lib/app.svelte";
  import AcctLabel from "$lib/components/AcctLabel.svelte";
  import AmountEdit from "$lib/components/AmountEdit.svelte";
  import { Badge } from "$lib/components/ui/badge";
  import { Button } from "$lib/components/ui/button";
  import { fmt, fmtDate, isoDay, nb, parseDate } from "$lib/format";
  import type { CardSummary } from "$lib/types";
  import { toast } from "svelte-sonner";

  // Each card's latest statement (click it to correct the bank's figure), when it's due and its usual spending.
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

{#if !cards.length}
  <p class="py-6 text-center text-sm text-muted-foreground">Link your cards through Plaid in <a class="font-medium text-foreground underline underline-offset-4" href="/#setup/connections">Settings → Connections</a> to see their statements and due dates.</p>
{:else}
  <div class="overflow-x-auto">
    <table class="w-full text-sm">
      <thead class="text-left text-xs text-muted-foreground">
        <tr class="[&>th]:pb-2 [&>th]:font-medium">
          <th>Card</th><th class="text-right">Statement</th><th class="text-right">Due</th>
          <th class="text-right" title="Average spending per statement over the last 3 statements; used to forecast future payments">Avg / stmt</th>
        </tr>
      </thead>
      <tbody>
        {#each cards as c (c.id)}
          {@const soon = c.remaining > 0 && (parseDate(c.due_date).getTime() - today.getTime()) / 864e5 <= 7}
          <tr class="border-t align-top [&>td]:py-2.5">
            <td><div class="font-medium"><AcctLabel id={c.id} name={c.name} /></div><div class="text-xs text-muted-foreground tabular-nums">owes {fmt(c.owed_now)} now</div></td>
            <td class="text-right">
              <AmountEdit amount={c.statement_balance} label="Statement balance" title={`Closed ${fmtDate(c.last_close)} · click to correct it`}
                save={(v) => setStatement(c, v)} />
              {#if c.statement_set}<Badge variant="secondary" title={`Entered by you · the bank reported ${fmt(c.statement_reported)}`}>set</Badge>{/if}
              <div class="text-xs text-muted-foreground tabular-nums">
                {#if c.remaining > 0}{c.remaining < c.statement_balance - 0.005 ? `${fmt(c.remaining)} left` : "unpaid"}{:else}<span class="text-emerald-500">Paid ✓</span>{/if}
                {#if c.remaining > 0 && c.minimum_payment} · {nb(`min ${fmt(c.minimum_payment)}`)}{/if}
                {#if c.statement_set} · <Button variant="link" size="sm" class="h-auto p-0" title={`Go back to the bank's figure (${fmt(c.statement_reported)})`} onclick={() => reset(c)}>reset</Button>{/if}
              </div>
            </td>
            <td class={["text-right tabular-nums", soon ? "font-medium text-amber-500" : "text-muted-foreground"]}>{fmtDate(c.due_date)}</td>
            <td class="text-right text-muted-foreground tabular-nums"
              title={c.avg_cycles ? `From the last ${c.avg_cycles} statement${c.avg_cycles === 1 ? "" : "s"}` : "Not enough history yet; using recent daily spending"}>
              {c.avg_monthly_spend != null ? fmt(c.avg_monthly_spend) : "—"}
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
{/if}
