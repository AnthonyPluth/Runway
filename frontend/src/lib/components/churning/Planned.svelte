<script lang="ts">
  import { api } from "$lib/api";
  import { Badge } from "$lib/components/ui/badge";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import EmptyLine from "$lib/components/EmptyLine.svelte";
  import { Input } from "$lib/components/ui/input";
  import { fmt0 } from "$lib/format";
  import { cn } from "$lib/utils";
  import CircleCheck from "@lucide/svelte/icons/circle-check";
  import ExternalLink from "@lucide/svelte/icons/external-link";
  import ArrowDown from "@lucide/svelte/icons/arrow-down";
  import ArrowUp from "@lucide/svelte/icons/arrow-up";
  import { toast } from "svelte-sonner";
  import { BLOCKER_LABEL, bonusLabel, fullDate, mine, reorder, scoreProgress, splitWishes, wishName } from "./churning";
  import type { Churning, Wish } from "./types";
  import WishForm from "./WishForm.svelte";

  // Cards and bank bonuses you mean to get, per person in the order you'd go: what each is expected to cost and pay,
  // what's in the way of applying now (5/24, the bank's bonus rules, an account still open, a credit score, a day you
  // set) and, when nothing is, "Ready to apply". "I applied" turns it into the real card or bonus.
  let { d, person, showOwner, onchanged, onapplied }: {
    d: Churning; person: string; showOwner: boolean; onchanged: () => void; onapplied: (kind: Wish["kind"], id: number) => void;
  } = $props();

  const wishes = $derived(mine(d.wishlist, person));
  const parts = $derived(splitWishes(wishes));
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
    try {
      await Promise.all(reorder(parts.open, w.id, dir).map((c) => api(`/api/churning/wishlist/${c.id}`, { method: "POST", body: { priority: c.priority } })));
      onchanged();
    } catch (err) { toast.error((err as Error).message); }
  }
  async function setStatus(w: Wish, status: "wanted" | "dropped") {
    try { await api(`/api/churning/wishlist/${w.id}`, { method: "POST", body: { status } }); toast(status === "dropped" ? `Dropped ${wishName(w)}` : `${wishName(w)} is wanted again`); onchanged(); }
    catch (err) { toast.error((err as Error).message); }
  }
  async function applied(w: Wish) {
    try {
      const r = await api<{ kind: Wish["kind"]; id: number }>(`/api/churning/wishlist/${w.id}/applied`, { method: "POST", body: {} });
      toast(`${wishName(w)} added. Set its details.`);
      onapplied(r.kind, r.id);
    } catch (err) { toast.error((err as Error).message); }
  }

  // A credit score you looked up.
  let scoring = $state<string | null>(null);
  let score = $state(""), scoreDay = $state(""), scoreSource = $state("");
  function openScore(p: string) {
    scoring = p; score = ""; scoreDay = d.today; scoreSource = d.scores[p]?.source ?? "";
  }
  async function saveScore() {
    try {
      await api("/api/churning/scores", { method: "POST", body: { owner: scoring, score, as_of: scoreDay, source: scoreSource } });
      scoring = null; onchanged();
    } catch (err) { toast.error((err as Error).message); }
  }
</script>

{#snippet item(w: Wish, n: number, count: number)}
  {@const sc = scoreProgress(w, d.scores)}
  {@const open = w.status === "wanted" || w.status === "ready"}
  <li class="py-3" data-wish={w.id}>
    <div class="flex flex-wrap items-start justify-between gap-x-3 gap-y-1">
      <div class="min-w-0">
        <div class="flex flex-wrap items-center gap-1.5 font-medium">
          {#if open}<span class="text-xs font-normal text-muted-foreground tabular-nums" title="Priority">#{n + 1}</span>{/if}
          {wishName(w)}
          <Badge variant="outline">{w.kind === "card" ? "Card" : "Bank bonus"}</Badge>
          {#if w.business}<Badge variant="outline">Business</Badge>{/if}
          {#if w.status === "applied"}<Badge variant="secondary">Applied{w.applied_on ? ` ${fullDate(w.applied_on)}` : ""}</Badge>
          {:else if w.status === "dropped"}<Badge variant="secondary">Dropped</Badge>{/if}
          {#if w.ready}<Badge class="border-transparent bg-[var(--good)]/15 text-[var(--good)]"><CircleCheck aria-hidden="true" />Ready to apply</Badge>{/if}
        </div>
        <div class="text-xs text-muted-foreground">
          {#if showOwner}{w.owner} · {/if}{#if w.kind === "card" && w.issuer}{d.issuers.find((i) => i.key === w.issuer)?.name ?? w.issuer} · {/if}{expect(w) || "No details yet"}
        </div>
      </div>
      <div class="flex shrink-0 flex-wrap items-center gap-1">
        {#if open}
          <Button variant="ghost" size="icon" class="size-8" disabled={n === 0} aria-label={`Move ${wishName(w)} up`} onclick={() => move(w, -1)}><ArrowUp class="size-4" /></Button>
          <Button variant="ghost" size="icon" class="size-8" disabled={n === count - 1} aria-label={`Move ${wishName(w)} down`} onclick={() => move(w, 1)}><ArrowDown class="size-4" /></Button>
          {#if w.apply_url}<Button size="sm" variant="outline" href={w.apply_url} target="_blank" rel="noopener noreferrer" aria-label={`Open the application for ${wishName(w)}`}>Apply<ExternalLink class="size-3.5" aria-hidden="true" /></Button>{/if}
          <Button size="sm" variant={w.ready ? "default" : "outline"} aria-label={`I applied for ${wishName(w)}`} onclick={() => applied(w)}>I applied</Button>
          <Button size="sm" variant="link" class="px-1" aria-label={`Edit ${wishName(w)}`} onclick={() => (form = w)}>Edit</Button>
          <Button size="sm" variant="link" class="px-1" aria-label={`Drop ${wishName(w)}`} onclick={() => setStatus(w, "dropped")}>Drop</Button>
        {:else if w.status === "dropped"}
          <Button size="sm" variant="link" class="px-1" onclick={() => setStatus(w, "wanted")}>Want again</Button>
          <Button size="sm" variant="link" class="px-1" onclick={() => (form = w)} aria-label={`Edit ${wishName(w)}`}>Edit</Button>
        {/if}
      </div>
    </div>
    {#if open}
      {#if w.blockers.length}
        <ul class="mt-1.5 space-y-0.5 text-sm" aria-label={`What's in the way of ${wishName(w)}`}>
          {#each w.blockers as b, i (i)}
            <li class="flex flex-wrap gap-x-1.5"><span class="text-xs font-medium text-[var(--warning)]">{BLOCKER_LABEL[b.kind]}</span><span>{b.text}{b.date ? ` (${fullDate(b.date)})` : ""}</span></li>
          {/each}
        </ul>
        {#if w.earliest_apply}<p class="mt-1 text-xs text-muted-foreground">Earliest you can apply: {fullDate(w.earliest_apply)}</p>{/if}
      {/if}
      {#if sc && !w.blockers.some((b) => b.text.includes(sc))}<p class="mt-1 text-xs font-medium">{sc}</p>{/if}
      {#each w.hints as h (h)}<p class="mt-1 text-xs text-muted-foreground italic">{h}</p>{/each}
      {#if w.offer_expires_on}<p class="mt-1 text-xs text-muted-foreground">Offer ends {fullDate(w.offer_expires_on)}</p>{/if}
    {/if}
  </li>
{/snippet}

{#if !parts.open.length && !parts.closed.length && !form && !scoring}
  <EmptyLine id="churning-planned" label="Planned" message="no cards or bank bonuses you’re eyeing" action="Plan a card or bonus" onaction={() => (form = "new")} />
{:else}
<Card.Root class="mb-6" id="churning-planned">
  <Card.Header>
    <Card.Title>Planned</Card.Title>
    <Card.Description>Cards and bank bonuses you want, in the order you'd go, and what's in the way of each.</Card.Description>
    <Card.Action><Button size="sm" variant="outline" onclick={() => (form = "new")}>Plan a card or bonus</Button></Card.Action>
  </Card.Header>
  <Card.Content>
    <div class="mb-3 flex flex-wrap gap-x-6 gap-y-1 text-sm" aria-label="Credit scores">
      {#each people as p (p)}
        {@const s = d.scores[p]}
        <span class="inline-flex flex-wrap items-center gap-x-2">
          <span class="text-muted-foreground">{p}'s credit score</span>
          <b class="font-medium tabular-nums">{s ? s.score : "not entered"}</b>
          {#if s}<span class="text-xs text-muted-foreground">as of {fullDate(s.as_of)}{s.source ? ` · ${s.source}` : ""}</span>{/if}
          <Button variant="link" size="sm" class="h-auto px-0" aria-label={`Update ${p}'s credit score`} onclick={() => openScore(p)}>{s ? "Update" : "Add"}</Button>
        </span>
      {/each}
    </div>
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
        {#each parts.open as w, n (w.id)}{@render item(w, n, parts.open.length)}{/each}
      </ul>
    {:else}
      <p class="py-4 text-center text-sm text-muted-foreground">Nothing planned. Add a card or bank bonus you're eyeing and it will tell you when you can apply.</p>
    {/if}
    {#if parts.closed.length}
      <Button variant="link" size="sm" class="mt-1 px-0" aria-expanded={showDone} onclick={() => (showDone = !showDone)}>{showDone ? "Hide" : "Show"} applied and dropped ({parts.closed.length})</Button>
      {#if showDone}<ul class={cn("divide-y text-muted-foreground")}>{#each parts.closed as w (w.id)}{@render item(w, 0, 0)}{/each}</ul>{/if}
    {/if}
  </Card.Content>
</Card.Root>
{/if}
