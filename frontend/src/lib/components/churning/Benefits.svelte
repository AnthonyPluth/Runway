<script lang="ts">
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import EmptyLine from "$lib/components/EmptyLine.svelte";
  import StatStrip, { type Stat } from "$lib/components/StatStrip.svelte";
  import { barWidth, fmt0 } from "$lib/format";
  import { cn } from "$lib/utils";
  import { benefitUnuse, benefitUse } from "./actions";
  import Chip from "./Chip.svelte";
  import { benefitBoard, benefitState, canUse, isPerk, lastUse, usesText, type BenefitRow } from "./churning";
  import MarkUsed from "./MarkUsed.svelte";
  import Popover from "./Popover.svelte";
  import type { ChurnCard } from "./types";

  // Every open card's benefits in one place: what's about to reset with money left, what's still to use, what you've
  // used this period, and the perks that are simply on (each lounge network, with who gets in free). Every row reads the
  // same way: the benefit, its card, where it stands, and on the right what it's worth a year and a button to mark it
  // used (a credit asks how much, the rest of it unless you change that) or, once used, to take that back. Edit a card to add or change its benefits.
  let { cards, showOwner, onchanged }: { cards: ChurnCard[]; showOwner: boolean; onchanged: () => void | Promise<void> } = $props();
  const board = $derived(benefitBoard(cards));
  const sections = $derived([
    { key: "expiring", title: "Expiring soon", rows: board.expiring },
    { key: "available", title: "Still to use", rows: board.available },
    { key: "used", title: "Used this period", rows: board.used },
    { key: "perks", title: "Lounges & perks", rows: board.perks },
  ]);
  const stats = $derived<Stat[]>([
    { label: "Expiring soon", value: String(board.expiring.length), tone: board.expiring.length ? "warn" : undefined },
    { label: "Credits left this period", value: fmt0(board.left) },
    { label: "Used this period", value: fmt0(board.usedAmount) },
    { label: "Worth a year", value: fmt0(board.value) },
  ]);
  const none = $derived(sections.every((s) => !s.rows.length));
  const who = (r: BenefitRow) => (showOwner ? `${r.card.product} (${r.card.owner})` : r.card.product);
  const worth = (r: BenefitRow) => (r.b.value_per_year ? `${fmt0(r.b.value_per_year)}/yr` : "—");
</script>

{#if none}
  <EmptyLine label="Benefits" message="none yet. Add them from a card’s Edit" />
{:else}
  <StatStrip class="mb-6" items={stats} />
  {#each sections as s (s.key)}
    {#if s.rows.length}
      <Card.Root class="mb-6">
        <Card.Header><Card.Title>{s.title} ({s.rows.length})</Card.Title></Card.Header>
        <Card.Content>
          <ul class="divide-y" aria-label={s.title}>
            {#each s.rows as r (r.b.id)}
              {@const uses = isPerk(r.b) ? "" : usesText(r.b)}
              <li class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 py-3">
                <div class="min-w-0 space-y-0.5">
                  <div class="flex flex-wrap items-center gap-1.5 text-sm font-medium">{r.b.name}{#if !r.b.counts}<Chip tone="neutral">Not counted</Chip>{/if}</div>
                  <div class="text-xs text-muted-foreground">{who(r)}</div>
                  <div class={cn("text-xs", s.key === "expiring" ? "font-medium text-warning" : "text-muted-foreground")}>
                    {benefitState(r.b)}{s.key === "expiring" && r.b.days_left != null ? ` · ${r.b.days_left} day${r.b.days_left === 1 ? "" : "s"} left` : ""}
                  </div>
                  {#if r.b.kind === "credit" && r.b.amount}
                    <div class="mt-1 h-1.5 overflow-hidden rounded-full bg-muted" role="progressbar" aria-label={`${r.b.name} on ${r.card.product} used this period`}
                      aria-valuemin={0} aria-valuemax={r.b.amount} aria-valuenow={r.b.used ?? 0}>
                      <div class="h-full rounded-full bg-[var(--nw-1)]" style:width={barWidth((r.b.used ?? 0) / r.b.amount)}></div>
                    </div>
                  {/if}
                  {#if uses}<div class="text-xs text-muted-foreground">{uses}</div>{/if}
                </div>
                <div class="flex flex-col items-end gap-1.5">
                  <span class="text-xs text-muted-foreground tabular-nums">{worth(r)}</span>
                  {#if !isPerk(r.b) && canUse(r.b)}
                    {#if r.b.kind === "credit" && r.b.amount != null}
                      <Popover label={`Mark ${r.b.name} on ${r.card.product} used`} variant="outline" class="px-3">
                        {#snippet trigger()}Mark used{/snippet}
                        {#snippet children(close)}<MarkUsed name={r.b.name} remaining={r.b.remaining} onuse={(amount) => { close(); benefitUse(r.b.id, r.b.name, amount, onchanged); }} />{/snippet}
                      </Popover>
                    {:else}
                      <Button size="sm" variant="outline" aria-label={`Mark ${r.b.name} on ${r.card.product} used`} onclick={() => benefitUse(r.b.id, r.b.name, null, onchanged)}>Mark used</Button>
                    {/if}
                  {:else if s.key === "used" && r.b.used_count > 0}
                    <Button size="sm" variant="ghost" aria-label={`Undo the use of ${r.b.name} on ${r.card.product}`} onclick={() => benefitUnuse(r.b.id, r.b.name, lastUse(r.b), onchanged)}>Undo</Button>
                  {/if}
                </div>
              </li>
            {/each}
          </ul>
        </Card.Content>
      </Card.Root>
    {/if}
  {/each}
{/if}
