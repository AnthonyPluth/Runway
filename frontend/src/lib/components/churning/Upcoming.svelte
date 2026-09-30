<script lang="ts">
  import { api } from "$lib/api";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { Input } from "$lib/components/ui/input";
  import { NativeSelect } from "$lib/components/ui/native-select";
  import { fmtDate, relDay } from "$lib/format";
  import { cn } from "$lib/utils";
  import { toast } from "svelte-sonner";
  import AlarmClock from "@lucide/svelte/icons/alarm-clock";
  import CalendarCheck from "@lucide/svelte/icons/calendar-check";
  import CreditCard from "@lucide/svelte/icons/credit-card";
  import FileCheck from "@lucide/svelte/icons/file-check";
  import Flag from "@lucide/svelte/icons/flag";
  import Landmark from "@lucide/svelte/icons/landmark";
  import ListChecks from "@lucide/svelte/icons/list-checks";
  import RotateCcw from "@lucide/svelte/icons/rotate-ccw";
  import Target from "@lucide/svelte/icons/target";
  import Ticket from "@lucide/svelte/icons/ticket";
  import { benefitUse, planDone, taskSnooze } from "./actions";
  import { KIND_LABEL, daysUntil } from "./churning";
  import type { ChurnCard, UpcomingItem } from "./types";

  // What's coming up, soonest first, with your own to-dos (tick one off when it's done) and a way to add one.
  let { items, cards, today, showOwner, onchanged }: {
    items: UpcomingItem[]; cards: ChurnCard[]; today: string; showOwner: boolean; onchanged: () => void;
  } = $props();

  let all = $state(false);
  const shown = $derived(all ? items : items.slice(0, 8));
  let adding = $state(false);
  let card = $state(""), due = $state(""), action = $state("");
  const SUGGEST = ["Close", "Downgrade (product change)", "Call for a retention offer", "Check the bonus posted", "Move spending elsewhere"];

  async function done(i: UpcomingItem) {
    try { await api(`/api/churning/tasks/${i.task_id}`, { method: "POST", body: { done: true } }); toast("Done"); onchanged(); }
    catch (err) { toast.error((err as Error).message); }
  }
  // An icon per kind, so a plan, a credit or a chance to apply stands out from a fee.
  const ICON = {
    task: ListChecks, fee: CreditCard, plan: Flag, bonus: Target, benefit: Ticket, five24: CalendarCheck, eligible: RotateCcw,
    apply: FileCheck, offer_ends: AlarmClock, bank_due: Landmark, bank_hold: Landmark, bank_post: Landmark, bank_close: Landmark,
    bank_fee: Landmark, bank_eligible: RotateCcw,
  } satisfies Record<UpcomingItem["kind"], unknown>;
  let snoozing = $state<number | null>(null);
  const SNOOZE = [{ days: 7, label: "1 week" }, { days: 14, label: "2 weeks" }, { days: 30, label: "1 month" }];
  const seePlanned = () => document.getElementById("churning-planned")?.scrollIntoView({ behavior: "smooth", block: "start" });
  async function add() {
    try {
      await api("/api/churning/tasks", { method: "POST", body: { card_id: card, due_on: due, action } });
      adding = false; card = ""; due = ""; action = "";
      onchanged();
    } catch (err) { toast.error((err as Error).message); }
  }
  const when = (d: string) => {
    const n = daysUntil(d, today);
    return n < 0 ? `${-n} day${n === -1 ? "" : "s"} overdue` : n < 7 ? relDay(d, today) : fmtDate(d);
  };
</script>

<Card.Root class="mb-6">
  <Card.Header>
    <Card.Title>Upcoming</Card.Title>
    <Card.Action><Button size="sm" variant="outline" onclick={() => (adding = !adding)} aria-expanded={adding}>Add a to-do</Button></Card.Action>
  </Card.Header>
  <Card.Content>
    {#if adding}
      <div class="mb-4 flex flex-wrap items-end gap-3 rounded-lg bg-muted/40 p-3">
        <label class="flex flex-col gap-1 text-sm">Card
          <NativeSelect bind:value={card}>
            <option value="">Choose…</option>
            {#each cards as c (c.id)}<option value={String(c.id)}>{c.product}{showOwner ? ` (${c.owner})` : ""}</option>{/each}
          </NativeSelect>
        </label>
        <label class="flex flex-col gap-1 text-sm">By<Input type="date" class="w-40" bind:value={due} /></label>
        <label class="flex min-w-52 flex-1 flex-col gap-1 text-sm">What to do
          <Input list="churn-actions" bind:value={action} placeholder="e.g. Downgrade to Freedom" />
          <datalist id="churn-actions">{#each SUGGEST as s (s)}<option value={s}></option>{/each}</datalist>
        </label>
        <Button size="sm" onclick={add}>Add</Button>
      </div>
    {/if}
    {#if items.length}
      <ul class="divide-y">
        {#each shown as i, n (`${i.kind}:${i.card_id ?? i.bank_id ?? i.owner}:${i.task_id ?? ""}:${i.date}:${n}`)}
          {@const late = daysUntil(i.date, today) < 0}
          {@const Icon = ICON[i.kind]}
          <li class="flex flex-wrap items-start gap-x-3 gap-y-1 py-2.5">
            <div class={cn("w-24 shrink-0 text-sm tabular-nums", late ? "font-medium text-red-500" : i.warn ? "font-medium text-[var(--warning)]" : "text-muted-foreground")}>{when(i.date)}</div>
            <div class="min-w-0 flex-1 max-sm:basis-[calc(100%-6.75rem)]">
              <div class="text-sm"><span class={cn("mr-1.5 inline-flex items-center gap-1 text-xs", i.kind === "apply" ? "text-[var(--good)]" : "text-muted-foreground")}><Icon class="size-3.5" aria-hidden="true" />{KIND_LABEL[i.kind]}</span>{i.title}</div>
              <div class="text-xs text-muted-foreground">{i.detail}{showOwner && i.owner && !i.title.startsWith(i.owner) ? ` · ${i.owner}` : ""}</div>
              {#if snoozing === i.task_id && i.task_id != null}
                <div class="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs" role="group" aria-label="Snooze for">
                  Snooze for
                  {#each SNOOZE as s (s.days)}<Button variant="outline" size="sm" class="h-6 px-2 text-xs" onclick={() => { snoozing = null; taskSnooze(i.task_id!, s.days, onchanged); }}>{s.label}</Button>{/each}
                </div>
              {/if}
            </div>
            <div class="flex shrink-0 flex-wrap justify-end gap-1.5 max-sm:basis-full max-sm:justify-start max-sm:pl-[6.75rem]">
              {#if i.kind === "task"}
                <Button variant="ghost" size="sm" aria-expanded={snoozing === i.task_id} onclick={() => (snoozing = snoozing === i.task_id ? null : (i.task_id ?? null))} aria-label={`Snooze "${i.title}"`}>Snooze</Button>
                <Button variant="outline" size="sm" onclick={() => done(i)} aria-label={`Mark "${i.title}" done`}>Done</Button>
              {:else if i.kind === "plan" && i.card_id != null}
                <Button variant="outline" size="sm" onclick={() => planDone(i.card_id!, onchanged)} aria-label={`Mark "${i.title}" done`}>Done</Button>
              {:else if i.kind === "benefit" && i.benefit_id != null}
                <Button variant="outline" size="sm" onclick={() => benefitUse(i.benefit_id!, i.title, null, onchanged)} aria-label={`Mark "${i.title}" used`}>Mark used</Button>
              {:else if i.kind === "apply" || i.kind === "offer_ends"}
                <Button variant="ghost" size="sm" onclick={seePlanned}>See planned</Button>
              {/if}
            </div>
          </li>
        {/each}
      </ul>
      {#if items.length > 8}
        <Button variant="link" size="sm" class="mt-1 px-0" onclick={() => (all = !all)}>{all ? "Show fewer" : `Show all ${items.length}`}</Button>
      {/if}
    {:else}<p class="py-4 text-center text-sm text-muted-foreground">Nothing in the next six months.</p>{/if}
  </Card.Content>
</Card.Root>
