<script lang="ts">
  import { api } from "$lib/api";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { ConfirmDialog } from "$lib/components/ui/confirm-dialog";
  import EmptyLine from "$lib/components/EmptyLine.svelte";
  import { Input } from "$lib/components/ui/input";
  import { fmt0 } from "$lib/format";
  import { cn } from "$lib/utils";
  import FoldedLine from "./FoldedLine.svelte";
  import CircleCheck from "@lucide/svelte/icons/circle-check";
  import ExternalLink from "@lucide/svelte/icons/external-link";
  import ArrowDown from "@lucide/svelte/icons/arrow-down";
  import ArrowUp from "@lucide/svelte/icons/arrow-up";
  import Ellipsis from "@lucide/svelte/icons/ellipsis";
  import { toast } from "svelte-sonner";
  import { wishDrop } from "./actions";
  import Chip from "./Chip.svelte";
  import { BLOCKER_LABEL, bonusLabel, fullDate, mine, reorder, scoreProgress, splitWishes, wishName } from "./churning";
  import Popover from "./Popover.svelte";
  import type { Churning, Wish } from "./types";
  import WishForm from "./WishForm.svelte";
  import { act } from "$lib/act";

  // Cards and bank bonuses you mean to get, everyone's in one list in the order you'd go: what each is expected to cost and pay,
  // what's in the way of applying now (5/24, the bank's bonus rules, an account still open, a credit score, a day you
  // set) and, when nothing is, "Ready to apply". "I applied" turns it into the real card or bonus.
  let { d, person, showOwner, onchanged, onapplied }: {
    d: Churning; person: string; showOwner: boolean; onchanged: () => void; onapplied: (kind: Wish["kind"], id: number) => void;
  } = $props();

  const wishes = $derived(mine(d.wishlist, person));
  const parts = $derived(splitWishes(wishes));
  const everyone = $derived(splitWishes(d.wishlist).open);   // what the order is saved over, whoever's shown
  let form = $state<Wish | "new" | null>(null);
  let showDone = $state(false);
  const people = $derived(person ? [person] : d.people);
  const closeForm = (changed: boolean) => { form = null; if (changed) onchanged(); };

  const cur = (key: string | null) => d.currencies.find((c) => c.key === key);
  // "$95 fee · 75k Ultimate Rewards after $4,000 in 3 months" or "$300 bonus · checking".
  function expect(w: Wish): string {
    const bits: string[] = [];
    if (w.kind === "card") {
      bits.push(w.annual_fee ? `${fmt0(w.annual_fee)} fee` : w.annual_fee === 0 ? "No fee" : "");
      const b = bonusLabel(w.bonus, w.currency ?? "cash", cur(w.currency)?.name ?? "");
      if (b) bits.push(`${b} bonus${w.bonus_spend ? ` after ${fmt0(w.bonus_spend)}${w.bonus_months ? ` in ${w.bonus_months} months` : ""}` : ""}`);
    } else {
      if (w.bonus) bits.push(`${fmt0(w.bonus)} bonus`);
      if (w.account_type) bits.push(w.account_type);
      if (w.requirements) bits.push(w.requirements);
    }
    return bits.filter(Boolean).join(" · ");
  }

  async function move(w: Wish, dir: -1 | 1) {
    await act(async () => {
      await Promise.all(reorder(everyone, w.id, dir, parts.open).map((c) => api(`/api/churning/wishlist/${c.id}`, { method: "POST", body: { priority: c.priority } })));
      onchanged();
    });
  }
  // Dropping can be undone from its toast; "Want again" is the way back from the list.
  async function wantAgain(w: Wish) {
    await act(async () => { await api(`/api/churning/wishlist/${w.id}`, { method: "POST", body: { status: "wanted" } }); toast(`${wishName(w)} is wanted again`); onchanged(); });
  }
  // Applied or dropped: deleted for good (what applying made, the card or bonus, stays), so it asks first.
  let deleting = $state<Wish | null>(null);
  let asking = $state(false);
  const askDelete = (w: Wish) => { deleting = w; asking = true; };
  async function remove(w: Wish): Promise<boolean> {
    return act(async () => { await api(`/api/churning/wishlist/${w.id}/remove`, { method: "POST", body: {} }); toast(`Deleted ${wishName(w)}`); onchanged(); });
  }
  async function applied(w: Wish) {
    await act(async () => {
      const r = await api<{ kind: Wish["kind"]; id: number }>(`/api/churning/wishlist/${w.id}/applied`, { method: "POST", body: {} });
      toast(`${wishName(w)} added. Set its details.`);
      onapplied(r.kind, r.id);
    });
  }

  // A credit score you looked up.
  let scoring = $state<string | null>(null);
  let score = $state(""), scoreDay = $state(""), scoreSource = $state("");
  // Credit scores only matter to a wish that wants one (or while one is being entered).
  const wantsScore = $derived(!!scoring || parts.open.some((w) => w.min_score != null));
  function openScore(p: string) {
    scoring = p; score = ""; scoreDay = d.today; scoreSource = d.scores[p]?.source ?? "";
  }
  async function saveScore() {
    await act(async () => {
      await api("/api/churning/scores", { method: "POST", body: { owner: scoring, score, as_of: scoreDay, source: scoreSource } });
      scoring = null; onchanged();
    });
  }
</script>

{#snippet item(w: Wish, n: number, count: number)}
  {@const sc = scoreProgress(w, d.scores)}
  {@const open = w.status === "wanted" || w.status === "ready"}
  <li class="py-3" data-wish={w.id}>
    <div class="flex flex-col gap-2 lg:flex-row lg:items-start lg:justify-between lg:gap-x-3">
      <div class="min-w-0">
        <div class="flex flex-wrap items-center gap-1.5 font-medium">
          {#if open}<span class="text-xs font-normal text-muted-foreground tabular-nums" title="Priority">#{n + 1}</span>{/if}
          {wishName(w)}
          <Chip>{w.kind === "card" ? "Card" : "Bank bonus"}</Chip>
          {#if w.business}<Chip>Business</Chip>{/if}
          {#if w.status === "applied"}<Chip tone="good">Applied{w.applied_on ? ` ${fullDate(w.applied_on, d.today)}` : ""}</Chip>
          {:else if w.status === "dropped"}<Chip tone="neutral">Dropped</Chip>{/if}
          {#if w.ready}<Chip tone="good"><CircleCheck aria-hidden="true" />Ready to apply</Chip>{/if}
        </div>
        <div class="text-xs text-muted-foreground">
          {#if showOwner}{w.owner} · {/if}{#if w.kind === "card" && w.issuer}{d.issuers.find((i) => i.key === w.issuer)?.name ?? w.issuer} · {/if}{expect(w) || "No details yet"}
        </div>
      </div>
      <div class="flex shrink-0 flex-wrap items-center gap-1">
        {#if open}
          {#if w.apply_url}<Button size="sm" variant="outline" href={w.apply_url} target="_blank" rel="noopener noreferrer" aria-label={`Open the application for ${wishName(w)}`}>Apply<ExternalLink class="size-3.5" aria-hidden="true" /></Button>{/if}
          <Button size="sm" variant={w.ready ? "default" : "outline"} aria-label={`I applied for ${wishName(w)}`} onclick={() => applied(w)}>I applied</Button>
          <Popover label={`More actions for ${wishName(w)}`}>
            {#snippet trigger()}<Ellipsis class="size-4" aria-hidden="true" />{/snippet}
            {#snippet children(close)}
              <Button size="sm" variant="ghost" class="justify-start" disabled={n === 0} aria-label={`Move ${wishName(w)} up`} onclick={() => { close(); move(w, -1); }}><ArrowUp class="size-4" aria-hidden="true" />Move up</Button>
              <Button size="sm" variant="ghost" class="justify-start" disabled={n === count - 1} aria-label={`Move ${wishName(w)} down`} onclick={() => { close(); move(w, 1); }}><ArrowDown class="size-4" aria-hidden="true" />Move down</Button>
              <Button size="sm" variant="ghost" class="justify-start" aria-label={`Edit ${wishName(w)}`} onclick={() => { close(); form = w; }}>Edit</Button>
              <Button size="sm" variant="ghost" class="justify-start" aria-label={`Drop ${wishName(w)}`} onclick={() => { close(); wishDrop(w.id, wishName(w), w.status, onchanged); }}>Drop</Button>
            {/snippet}
          </Popover>
        {:else if w.status === "dropped"}
          <Button size="sm" variant="outline" onclick={() => wantAgain(w)}>Want again</Button>
          <Popover label={`More actions for ${wishName(w)}`}>
            {#snippet trigger()}<Ellipsis class="size-4" aria-hidden="true" />{/snippet}
            {#snippet children(close)}
              <Button size="sm" variant="ghost" class="justify-start" aria-label={`Edit ${wishName(w)}`} onclick={() => { close(); form = w; }}>Edit</Button>
              <Button size="sm" variant="ghost" class="justify-start" aria-label={`Delete ${wishName(w)}`} onclick={() => { close(); askDelete(w); }}>Delete</Button>
            {/snippet}
          </Popover>
        {:else}
          <Button size="sm" variant="link" class="px-1" aria-label={`Delete ${wishName(w)}`} onclick={() => askDelete(w)}>Delete</Button>
        {/if}
      </div>
    </div>
    {#if open}
      {#if w.blockers.length}
        <ul class="mt-1.5 space-y-0.5 text-sm" aria-label={`What's in the way of ${wishName(w)}`}>
          {#each w.blockers as b, i (i)}
            <li class="flex flex-wrap gap-x-1.5"><span class="text-xs font-medium text-warning">{BLOCKER_LABEL[b.kind]}</span><span>{b.text}{b.date ? ` (${fullDate(b.date, d.today)})` : ""}</span></li>
          {/each}
        </ul>
        {#if w.earliest_apply}<p class="mt-1 text-xs text-muted-foreground">Earliest you can apply: {fullDate(w.earliest_apply, d.today)}</p>{/if}
      {/if}
      {#if sc && !w.blockers.some((b) => b.text.includes(sc))}<p class="mt-1 text-xs font-medium">{sc}</p>{/if}
      {#each w.hints as h (h)}<p class="mt-1 text-xs text-muted-foreground italic">{h}</p>{/each}
      {#if w.offer_expires_on}<p class="mt-1 text-xs text-muted-foreground">Offer ends {fullDate(w.offer_expires_on, d.today)}</p>{/if}
    {/if}
  </li>
{/snippet}

{#if !parts.open.length && !parts.closed.length && !form && !scoring}
  <EmptyLine id="churning-planned" label="Planned" message="no cards or bank bonuses you’re eyeing" action="Plan a card or bonus" onaction={() => (form = "new")} />
{:else}
<Card.Root class="mb-6" id="churning-planned">
  <Card.Header>
    <Card.Title>Planned</Card.Title>
    <Card.Action><Button size="sm" variant="outline" onclick={() => (form = "new")}>Plan a card or bonus</Button></Card.Action>
  </Card.Header>
  <Card.Content>
    {#if wantsScore}
      <div class="mb-3 flex flex-wrap gap-x-6 gap-y-1 text-sm" aria-label="Credit scores">
        {#each people as p (p)}
          {@const s = d.scores[p]}
          <span class="inline-flex flex-wrap items-center gap-x-2">
            <span class="text-muted-foreground">{p}'s credit score</span>
            <b class="font-medium tabular-nums">{s ? s.score : "not entered"}</b>
            {#if s}<span class="text-xs text-muted-foreground">as of {fullDate(s.as_of, d.today)}{s.source ? ` · ${s.source}` : ""}</span>{/if}
            <Button variant="link" size="sm" class="h-auto px-0" aria-label={`Update ${p}'s credit score`} onclick={() => openScore(p)}>{s ? "Update" : "Add"}</Button>
          </span>
        {/each}
      </div>
    {/if}
    {#if scoring}
      <div class="mb-4 flex flex-wrap items-end gap-3 rounded-lg bg-muted/40 p-3">
        <label class="flex flex-col gap-1 text-sm">{scoring}'s score<Input type="number" min="300" max="900" class="w-24" bind:value={score} placeholder="e.g. 720" /></label>
        <label class="flex flex-col gap-1 text-sm">As of<Input type="date" class="w-40" bind:value={scoreDay} /></label>
        <label class="flex flex-col gap-1 text-sm">Source<Input class="w-40" bind:value={scoreSource} placeholder="e.g. Credit Karma" /></label>
        <Button size="sm" onclick={saveScore}>Save score</Button><Button variant="link" size="sm" onclick={() => (scoring = null)}>Cancel</Button>
      </div>
    {/if}
    {#if form}{#key form}<WishForm w={form === "new" ? null : form} {d} {person} onclose={closeForm} />{/key}{/if}
    {#if parts.open.length}
      <ul class="divide-y">
        {#each parts.open as w, i (w.id)}{@render item(w, i, parts.open.length)}{/each}
      </ul>
    {/if}
    {#if parts.closed.length}
      <FoldedLine class="mt-1" count={parts.closed.length} noun="applied or dropped" bind:open={showDone} />
      {#if showDone}<ul class={cn("divide-y text-muted-foreground")}>{#each parts.closed as w (w.id)}{@render item(w, 0, 0)}{/each}</ul>{/if}
    {/if}
  </Card.Content>
</Card.Root>
{/if}

{#if deleting}
  <ConfirmDialog bind:open={asking} title={`Delete ${wishName(deleting)}?`} confirmLabel="Delete" busyLabel="Deleting…" destructive
    description={deleting.status === "applied" ? "It’s deleted for good. The card or bank bonus it became stays." : "It’s deleted for good."}
    onconfirm={() => remove(deleting!)} />
{/if}
