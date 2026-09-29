<script lang="ts">
  import { Badge } from "$lib/components/ui/badge";
  import { Button } from "$lib/components/ui/button";
  import { fmt0, fmtDate } from "$lib/format";
  import { cn } from "$lib/utils";
  import { BANK_STATUS_LABEL, BANK_TYPE_LABEL, bankLeft, eligibilityText, fullDate, spendProgress } from "./churning";
  import type { BankBonus, Churning } from "./types";

  // Bank account bonuses, one row each: the bank, the bonus and where its requirements stand, and its key dates.
  let { bonuses, d, showOwner, onedit }: { bonuses: BankBonus[]; d: Churning; showOwner: boolean; onedit: (b: BankBonus) => void } = $props();
  const cols = "md:grid md:grid-cols-[minmax(0,1.4fr)_minmax(0,1.8fr)_minmax(0,1.3fr)_auto] md:items-center md:gap-4";
</script>

<div class={cn("hidden border-b pb-2 text-xs font-medium tracking-wide text-muted-foreground uppercase", cols)} aria-hidden="true">
  <span>Account</span><span>Requirements</span><span>Dates</span><span></span>
</div>
<ul class="divide-y">
  {#each bonuses as b (b.id)}
    {@const left = bankLeft(b)}
    {@const p = b.progress}
    {@const dd = spendProgress(p.dd_total, b.dd_total)}
    {@const done = b.state === "received" || b.state === "closed"}
    <li class={cn("relative flex flex-col gap-2 py-3", cols, done && "text-muted-foreground")}>
      <div class="min-w-0 max-md:pr-12">
        <div class="flex flex-wrap items-center gap-1.5 font-medium">
          <span class={cn(done ? "text-muted-foreground" : "text-foreground")}>{b.bank} {BANK_TYPE_LABEL[b.account_type].toLowerCase()}</span>
          <span class="tabular-nums">{fmt0(b.received_amount ?? b.bonus)}</span>
          {#if b.state === "received"}<Badge variant="secondary">Received {fmtDate(b.received_on!, { month: "short", year: "numeric" })}</Badge>
          {:else if b.state === "met"}<Badge variant="outline">{BANK_STATUS_LABEL.pending}</Badge>
          {:else if b.state === "missed"}<Badge variant="destructive">Missed</Badge>
          {:else if b.state === "closed"}<Badge variant="secondary">Closed</Badge>{/if}
        </div>
        <div class="text-xs text-muted-foreground">{showOwner ? `${b.owner} · ` : ""}opened {fullDate(b.opened_on)}{b.monthly_fee ? ` · ${fmt0(b.monthly_fee)}/month fee${b.fee_waiver ? `, waived by ${b.fee_waiver}` : ""}` : ""}</div>
      </div>
      <div class={cn("min-w-0 text-sm", done && "max-md:hidden")}>
        {#if b.state === "active" || b.state === "missed"}
          {#if b.dd_total}
            <div class="flex items-baseline justify-between gap-2 text-xs">
              <span class="text-foreground">Direct deposits</span>
              <span class="text-muted-foreground tabular-nums">{fmt0(p.dd_total)} of {fmt0(b.dd_total)}{b.dd_count && p.dd_count != null ? ` · ${p.dd_count} of ${b.dd_count}` : ""}</span>
            </div>
            <div class="mt-1 h-1.5 overflow-hidden rounded-full bg-muted" role="progressbar" aria-label={`Direct deposits toward the ${b.bank} bonus`}
              aria-valuemin={0} aria-valuemax={b.dd_total} aria-valuenow={p.dd_total}>
              <div class={cn("h-full rounded-full", dd.share >= 1 ? "bg-[var(--good)]" : "bg-[var(--nw-2)]")} style:width={`${(dd.share * 100).toFixed(1)}%`}></div>
            </div>
          {/if}
          <div class="mt-1 text-xs text-muted-foreground">
            {left.length ? left.join(" · ") : "Tracked requirements met"}{b.other_reqs ? ` · ${b.other_reqs}` : ""}
            {#if b.min_balance && p.balance != null && p.balance_ok} · balance {fmt0(p.balance)} ✓{/if}
            {#if p.source === "manual"} · progress you entered{/if}
          </div>
        {:else if b.state === "met"}<span class="text-xs">Waiting for {fmt0(b.bonus)} to post{b.expected_on ? `, by ${fullDate(b.expected_on)}` : ""}</span>
        {:else}<span class="text-xs">—</span>{/if}
      </div>
      <div class="text-xs leading-relaxed">
        {#if b.state === "active"}<div>Due <span class="text-foreground">{fullDate(b.due)}</span></div>{/if}
        {#if b.safe_close_on}<div>Safe to close <span class="text-foreground">{fullDate(b.safe_close_on)}</span></div>{/if}
        {#if b.state !== "active" && b.state !== "met"}<div title={b.eligibility.why}>Bonus again: <span class={cn(b.eligibility.status === "now" ? "text-[var(--good)]" : "text-foreground")}>{eligibilityText(b.eligibility, d.today)}</span></div>{/if}
      </div>
      <div class="max-md:absolute max-md:top-2 max-md:right-0 md:text-right"><Button variant="link" size="sm" class="h-auto px-0" onclick={() => onedit(b)} aria-label={`Edit ${b.bank}`}>Edit</Button></div>
    </li>
  {/each}
</ul>
