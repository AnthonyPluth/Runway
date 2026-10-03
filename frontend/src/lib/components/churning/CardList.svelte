<script lang="ts">
  import { Button } from "$lib/components/ui/button";
  import { barWidth, fmt0, fmtDate } from "$lib/format";
  import { cn } from "$lib/utils";
  import ChevronRight from "@lucide/svelte/icons/chevron-right";
  import { planDone, planUndo } from "./actions";
  import Chip from "./Chip.svelte";
  import { CARD_STATUS_TONE, STATUS_LABEL, bonusLabel, eligibilityText, fullDate, netFeeText, perDayNeeded, planLine, spendProgress, urgency } from "./churning";
  import type { ChurnCard, Churning } from "./types";

  // The cards, one compact row each: the card, its annual fee, the bonus and its spending, when the bonus can be earned
  // again. The rest (the bank, when it was opened, 5/24, your plan) folds out under the row's chevron; a plan that's
  // due shows as a chip so it isn't missed. A deadline (the fee, the bonus) is red within 7 days and amber within 30, as
  // everywhere on the page. A table's columns from lg up (the sidebar takes a quarter of a tablet's width); stacked below.
  let { cards, d, showOwner, onedit, onchanged }: {
    cards: ChurnCard[]; d: Churning; showOwner: boolean; onedit: (c: ChurnCard) => void; onchanged: () => void;
  } = $props();
  const issuer = (k: string) => d.issuers.find((i) => i.key === k)?.name ?? k;
  const cols = "lg:grid lg:grid-cols-[minmax(0,1.6fr)_minmax(0,0.9fr)_minmax(0,1.6fr)_minmax(0,1.1fr)_auto] lg:items-center lg:gap-4";
  let openId = $state<number | null>(null);
  // The spending bar takes the deadline's tone: red and amber as the date nears, the usual blue otherwise, green once it's all spent.
  const BAR = { bad70: "bg-loss/70", good: "bg-good", bad: "bg-loss", warn: "bg-warning", muted: "bg-[var(--nw-1)]" };
</script>

<div class={cn("hidden border-b pb-2 text-xs font-semibold tracking-[0.14em] text-muted-foreground uppercase", cols)} aria-hidden="true">
  <span class="pl-5">Card</span><span>Annual fee</span><span>Sign-up bonus</span><span>Bonus again</span><span></span>
</div>
<ul class="divide-y">
  {#each cards as c (c.id)}
    {@const p = spendProgress(c.spent, c.bonus_spend)}
    {@const fee = c.fee_due && c.status === "open" ? urgency(c.fee_due, d.today) : null}
    {@const bonus = bonusLabel(c.bonus, c.currency, c.currency_name)}
    {@const net = netFeeText(c)}
    {@const due = c.bonus_state === "active" && c.deadline ? urgency(c.deadline, d.today) : null}
    {@const perDay = due ? perDayNeeded(p.left, c.deadline!, d.today) : ""}
    {@const plan = planLine(c, d.today)}
    {@const open = openId === c.id}
    <li class={cn("relative py-2", c.status !== "open" && "text-muted-foreground")}>
      <div class={cn("flex flex-col gap-1", cols)}>
        <div class="flex min-w-0 items-center gap-1 max-lg:pr-12">
          <button type="button" class="-ml-1 flex size-6 shrink-0 cursor-pointer items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
            aria-expanded={open} aria-label={`${c.product} details`} onclick={() => (openId = open ? null : c.id)}>
            <ChevronRight class={cn("size-4 transition-transform", open && "rotate-90")} aria-hidden="true" />
          </button>
          <div class="flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-0.5 font-medium text-foreground">
            <span class={cn("truncate", c.status !== "open" && "text-muted-foreground")}>{c.product}</span>
            {#if showOwner}<span class="text-xs font-normal text-muted-foreground">{c.owner}</span>{/if}
            {#if c.status !== "open"}<Chip tone={CARD_STATUS_TONE[c.status]}>{STATUS_LABEL[c.status]}</Chip>{/if}
            {#if c.authorized_user}<Chip>AU</Chip>{/if}
            {#if c.business}<Chip>Business</Chip>{/if}
            {#if c.plan_active && plan}<Chip tone="warn" title={`Plan: ${plan}`}>Plan</Chip>{/if}
          </div>
        </div>
        <div class="text-sm max-lg:pl-6">
          <span class="text-xs text-muted-foreground lg:hidden">Annual fee </span>
          {#if !c.annual_fee}<span class="text-muted-foreground">No fee</span>
          {:else}{fmt0(c.annual_fee)}{#if c.fee_due}<span class={cn("ml-1.5 text-xs whitespace-nowrap", fee ? [fee.cls, fee.tone !== "muted" && "font-medium"] : "text-muted-foreground")} title={fee?.label}>due {fullDate(c.fee_due, d.today)}</span>{/if}
            {#if net}<div class="text-xs text-muted-foreground">{net}</div>{/if}{/if}
        </div>
        <div class={cn("min-w-0 text-sm max-lg:pl-6", !c.bonus_state && "max-lg:hidden")}>
          {#if !c.bonus_state}<span class="text-muted-foreground">—</span>
          {:else if c.bonus_state === "earned"}<span class="text-good">{bonus || "Bonus"} earned</span> <span class="text-xs text-muted-foreground">{fmtDate(c.bonus_earned_on!, { month: "short", year: "numeric" })}</span>
          {:else}
            <div title={due ? [due.label, perDay].filter(Boolean).join(" · ") : undefined}>
            <div class="flex items-baseline justify-between gap-2 text-xs">
              <span class="text-foreground">{bonus}</span>
              <span class={cn("tabular-nums", c.bonus_state === "missed" ? "text-loss" : "text-muted-foreground")}
                title={c.spend_source === "manual" && c.bonus_state === "active" ? "Spending you entered" : undefined}>
                {#if c.bonus_state === "met"}Spent · waiting to post{:else if c.bonus_state === "missed"}Missed{:else}{fmt0(c.spent)} of {fmt0(c.bonus_spend)} · <span class={cn(due && [due.cls, due.tone !== "muted" && "font-medium"])}>by {fmtDate(c.deadline!)}</span>{/if}
              </span>
            </div>
            <div class="mt-0.5 h-1 overflow-hidden rounded-full bg-muted" role="progressbar" aria-label={`Spending toward the ${c.product} bonus`}
              aria-valuemin={0} aria-valuemax={c.bonus_spend ?? 0} aria-valuenow={c.spent ?? 0}>
              <div class={cn("h-full rounded-full", BAR[c.bonus_state === "missed" ? "bad70" : p.share >= 1 ? "good" : (due?.tone ?? "muted")])} style:width={barWidth(p.share)}></div>
            </div>
            </div>
          {/if}
        </div>
        <div class={cn("text-sm max-lg:pl-6", c.eligibility.status === "au" && "max-lg:hidden")} title={c.eligibility.rule ? `${c.eligibility.why}. ${c.eligibility.issuer}: ${c.eligibility.rule} A rule of thumb: confirm with the bank.` : c.eligibility.why}>
          <span class="text-xs text-muted-foreground lg:hidden">Bonus again </span>
          <span class={cn(c.eligibility.status === "now" && "text-good")}>{eligibilityText(c.eligibility, d.today)}</span>
        </div>
        <div class="max-lg:absolute max-lg:top-2 max-lg:right-0 lg:text-right"><Button variant="link" size="sm" class="h-auto px-0" onclick={() => onedit(c)} aria-label={`Edit ${c.product}`}>Edit</Button></div>
      </div>
      {#if open}
        <div class="mt-1.5 flex flex-col gap-1 pl-6 text-xs text-muted-foreground" data-testid={`card-details-${c.id}`}>
          <span>{issuer(c.issuer)} · {c.owner}{c.authorized_user ? " (authorized user)" : ""} · opened {fmtDate(c.opened_on, { month: "short", year: "numeric" })} · {c.counts_524 ? `counts toward 5/24 until ${fullDate(c.falls_off, d.today)}` : "doesn’t count toward 5/24"}</span>
          {#if c.spend_source === "manual" && c.bonus_state === "active"}<span>Bonus spending is what you entered</span>{/if}
          {#if plan}
            <span class="inline-flex flex-wrap items-center gap-1.5">
              Plan: <span class={cn(c.plan_active && "font-medium text-foreground")}>{plan}</span>
              {#if c.plan_done_on}<Button variant="link" size="sm" class="h-auto px-0 text-xs" aria-label={`Undo the plan for ${c.product}`} onclick={() => planUndo(c.id, onchanged)}>Undo</Button>
              {:else if c.plan_active}<Button variant="outline" size="sm" class="h-6 px-2 text-xs" aria-label={`Mark the plan for ${c.product} done`} onclick={() => planDone(c.id, onchanged)}>Done</Button>{/if}
            </span>
          {/if}
          {#if c.hide_upcoming}<span>Hidden from Upcoming</span>{/if}
        </div>
      {/if}
    </li>
  {/each}
</ul>
