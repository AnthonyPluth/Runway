<script lang="ts">
  import { Badge } from "$lib/components/ui/badge";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import EmptyLine from "$lib/components/EmptyLine.svelte";
  import StatStrip, { type Stat } from "$lib/components/StatStrip.svelte";
  import { fmt0 } from "$lib/format";
  import { cn } from "$lib/utils";
  import { benefitUse } from "./actions";
  import { benefitBoard, benefitState, canUse, isPerk, usesText, type BenefitRow } from "./churning";
  import type { ChurnCard } from "./types";

  // Every open card's benefits in one place: what's about to reset with money left, what's still to use, what you've
  // used this period, and the perks that are simply on (each lounge network, with who gets in free). Every row reads the
  // same way: the benefit, its card, where it stands, and on the right what it's worth a year and a button to mark it
  // used. Edit a card to add or change its benefits.
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
  <EmptyLine label="Benefits" message="none yet" />
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
                  <div class="flex flex-wrap items-center gap-1.5 text-sm font-medium">{r.b.name}{#if !r.b.counts}<Badge variant="secondary">Not counted</Badge>{/if}</div>
                  <div class="text-xs text-muted-foreground">{who(r)}</div>
                  <div class={cn("text-xs", s.key === "expiring" ? "font-medium text-[var(--warning)]" : "text-muted-foreground")}>
                    {benefitState(r.b)}{s.key === "expiring" && r.b.days_left != null ? ` · ${r.b.days_left} day${r.b.days_left === 1 ? "" : "s"} left` : ""}
                  </div>
                  {#if uses}<div class="text-xs text-muted-foreground">{uses}</div>{/if}
                </div>
                <div class="flex flex-col items-end gap-1.5">
                  <span class="text-xs text-muted-foreground tabular-nums">{worth(r)}</span>
                  {#if !isPerk(r.b) && canUse(r.b)}
                    <Button size="sm" variant="outline" aria-label={`Mark ${r.b.name} on ${r.card.product} used`} onclick={() => benefitUse(r.b.id, r.b.name, null, onchanged)}>Mark used</Button>
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
