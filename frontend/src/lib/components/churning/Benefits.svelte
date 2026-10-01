<script lang="ts">
  import { Badge } from "$lib/components/ui/badge";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { fmt0 } from "$lib/format";
  import { isPhone } from "$lib/phone.svelte";
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
    { key: "expiring", title: "Expiring soon", note: "Money left, and the period ends soon.", rows: board.expiring },
    { key: "available", title: "Still to use", note: "Not used up this period.", rows: board.available },
    { key: "used", title: "Used this period", note: "Nothing left to claim until it resets.", rows: board.used },
    { key: "perks", title: "Lounges & perks", note: "On all year: lounge networks with who gets in free, bags and status.", rows: board.perks },
  ]);
  const stats = $derived([
    { label: "Expiring soon", value: String(board.expiring.length), tone: board.expiring.length ? "text-[var(--warning)]" : "" },
    { label: "Credits left this period", value: fmt0(board.left), tone: "" },
    { label: "Used this period", value: fmt0(board.usedAmount), tone: "" },
    { label: "Worth a year", value: fmt0(board.value), tone: "" },
  ]);
  const none = $derived(sections.every((s) => !s.rows.length));
  const who = (r: BenefitRow) => (showOwner ? `${r.card.product} (${r.card.owner})` : r.card.product);
  const worth = (r: BenefitRow) => (r.b.value_per_year ? `${fmt0(r.b.value_per_year)}/yr` : "—");
</script>

<div class="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
  {#each stats as s (s.label)}
    <Card.Root class="gap-1 py-3">
      <Card.Header class="px-4"><Card.Description>{s.label}</Card.Description><Card.Title class={cn("text-xl tabular-nums", s.tone)}>{s.value}</Card.Title></Card.Header>
    </Card.Root>
  {/each}
</div>

{#if none}
  <Card.Root class="mb-6"><Card.Content><p class="py-6 text-center text-sm text-muted-foreground">{isPhone() ? "No benefits yet. Open Runway on a computer to add a card’s lounge access and credits." : "No benefits yet. Edit a card and add its lounge access and credits to track them here."}</p></Card.Content></Card.Root>
{:else}
  {#each sections as s (s.key)}
    {#if s.rows.length}
      <Card.Root class="mb-6">
        <Card.Header><Card.Title>{s.title} ({s.rows.length})</Card.Title><Card.Description>{s.note}</Card.Description></Card.Header>
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
