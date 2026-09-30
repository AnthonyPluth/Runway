<script lang="ts">
  import { Badge } from "$lib/components/ui/badge";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { fmt0, fmtDate } from "$lib/format";
  import { cn } from "$lib/utils";
  import { benefitUse } from "./actions";
  import { benefitBoard, benefitState, canUse, type BenefitRow } from "./churning";
  import type { ChurnCard } from "./types";

  // Every open card's benefits in one place: what's about to reset with money left, what's still to use, and what you've
  // used this period. Marking one used works here as on the card (Edit a card to add or change its benefits).
  let { cards, showOwner, onchanged }: { cards: ChurnCard[]; showOwner: boolean; onchanged: () => void | Promise<void> } = $props();
  const board = $derived(benefitBoard(cards));
  const sections = $derived([
    { key: "expiring", title: "Expiring soon", note: "Money left, and the period ends soon.", rows: board.expiring },
    { key: "available", title: "Still to use", note: "Not used up this period.", rows: board.available },
    { key: "used", title: "Used this period", note: "Nothing left to claim until it resets.", rows: board.used },
  ]);
  const stats = $derived([
    { label: "Expiring soon", value: String(board.expiring.length), tone: board.expiring.length ? "text-[var(--warning)]" : "" },
    { label: "Credits left this period", value: fmt0(board.left), tone: "" },
    { label: "Used this period", value: fmt0(board.usedAmount), tone: "" },
    { label: "Worth a year", value: fmt0(board.value), tone: "" },
  ]);
  const none = $derived(!board.expiring.length && !board.available.length && !board.used.length);
  const who = (r: BenefitRow) => (showOwner ? `${r.card.product} (${r.card.owner})` : r.card.product);
</script>

<div class="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
  {#each stats as s (s.label)}
    <Card.Root class="gap-1 py-3">
      <Card.Header class="px-4"><Card.Description>{s.label}</Card.Description><Card.Title class={cn("text-xl tabular-nums", s.tone)}>{s.value}</Card.Title></Card.Header>
    </Card.Root>
  {/each}
</div>

{#if none}
  <Card.Root class="mb-6"><Card.Content><p class="py-6 text-center text-sm text-muted-foreground">No benefits yet. Edit a card and add its lounge access and credits to track them here.</p></Card.Content></Card.Root>
{:else}
  {#each sections as s (s.key)}
    {#if s.rows.length}
      <Card.Root class="mb-6">
        <Card.Header><Card.Title>{s.title} ({s.rows.length})</Card.Title><Card.Description>{s.note}</Card.Description></Card.Header>
        <Card.Content>
          <ul class="divide-y">
            {#each s.rows as r (r.b.id)}
              <li class="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 py-2.5">
                <div class="min-w-0">
                  <div class="flex flex-wrap items-center gap-1.5 text-sm font-medium">{r.b.name}{#if !r.b.counts}<Badge variant="secondary">Not counted</Badge>{/if}</div>
                  <div class="text-xs text-muted-foreground">{who(r)}</div>
                  <div class={cn("text-xs", s.key === "expiring" ? "font-medium text-[var(--warning)]" : "text-muted-foreground")}>{benefitState(r.b)}{s.key === "expiring" && r.b.days_left != null ? ` · ${r.b.days_left} day${r.b.days_left === 1 ? "" : "s"} left` : ""}</div>
                  {#if r.b.uses.length}
                    <div class="text-xs text-muted-foreground">{r.b.uses.map((u) => `${u.amount_used != null ? fmt0(u.amount_used) : "Used"} on ${fmtDate(u.used_on)}`).join(", ")}</div>
                  {/if}
                </div>
                {#if canUse(r.b)}
                  <Button size="sm" variant="outline" aria-label={`Mark ${r.b.name} on ${r.card.product} used`} onclick={() => benefitUse(r.b.id, r.b.name, null, onchanged)}>Mark used</Button>
                {/if}
              </li>
            {/each}
          </ul>
        </Card.Content>
      </Card.Root>
    {/if}
  {/each}
{/if}
