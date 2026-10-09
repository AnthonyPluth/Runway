<script lang="ts">
  import { commas } from "$lib/commas";
  import { api } from "$lib/api";
  import { app } from "$lib/app.svelte";
  import { autosave } from "$lib/autosave";
  import AiButton from "$lib/components/AiButton.svelte";
  import OwnerSelect from "$lib/components/OwnerSelect.svelte";
  import { Button } from "$lib/components/ui/button";
  import { ConfirmDialog } from "$lib/components/ui/confirm-dialog";
  import { Input } from "$lib/components/ui/input";
  import { NativeSelect } from "$lib/components/ui/native-select";
  import { onMount } from "svelte";
  import { fromAction } from "svelte/attachments";
  import { toast } from "svelte-sonner";
  import { planDone, planUndo } from "./actions";
  import BenefitDrafts from "./BenefitDrafts.svelte";
  import CardBenefits from "./CardBenefits.svelte";
  import { PLAN_ACTS, PLAN_LABEL, benefitsSummary, bonusSummary, fullDate, ratesPayload, ratesSummary, type RateRow } from "./churning";
  import CurrencySelect from "./CurrencySelect.svelte";
  import FieldNote from "./FieldNote.svelte";
  import { fieldProps } from "./form";
  import FormFooter from "./FormFooter.svelte";
  import RatesEditor from "./RatesEditor.svelte";
  import Section from "./Section.svelte";
  import type { ChurnCard, Churning, FoundDraft } from "./types";
  import { validateCard } from "./validate";
  import { act } from "$lib/act";
  import { AddForm } from "./addForm.svelte";
  import { CardSuggest, sourceHost } from "./cardSuggest.svelte";

  // Adding a card, or editing one (each field saves as you change it, as elsewhere in Runway; Close redraws the page).
  // The essentials (whose card, bank, name, opened, annual fee) are always showing; the rest sits in sections that start
  // closed, each saying what's in it, and open for the AI's suggestions or a refused save. The annual fee posts in the
  // month the card was opened, so there's no month to pick.
  // `onchanged` redraws the page's data without closing the form (a benefit was used, a plan checked off).
  // `draft`: a credit card account found on your accounts (Churning's "Found on your accounts"), to review and add.
  let { c, d, person, draft = null, onclose, onchanged }: {
    c: ChurnCard | null; d: Churning; person: string; draft?: FoundDraft | null; onclose: (changed: boolean) => void; onchanged: () => void | Promise<void>;
  } = $props();

  const str = (x: unknown) => (x == null ? "" : String(x));
  // The fields as they start (the form is drawn afresh for each card).
  const initial = () => {
    const start = c;
    return {
      owner: start?.owner ?? (draft?.owner || person || d.people[0] || ""), issuer: start?.issuer ?? draft?.issuer ?? "chase",
      product: str(start?.product ?? draft?.product),
      family: str(start?.family), opened_on: str(start?.opened_on) || draft?.opened_on || d.today, status: start?.status ?? "open",
      closed_on: str(start?.closed_on), changed_from: str(start?.changed_from), authorized_user: !!start?.authorized_user,
      business: !!(start?.business ?? draft?.business), annual_fee: str(start?.annual_fee || draft?.annual_fee || ""),
      currency: start?.currency ?? "cash", base_rate: str(start?.base_rate ?? 1), earn_note: str(start?.earn_note),
      account_id: str(start?.account_id ?? draft?.account_id), bonus: str(start?.bonus), bonus_spend: str(start?.bonus_spend),
      bonus_months: str(start?.bonus_months ?? 3), bonus_deadline: str(start?.bonus_deadline), manual_spend: str(start?.manual_spend),
      bonus_earned_on: str(start?.bonus_earned_on), eligible_on: str(start?.eligible_on), notes: str(start?.notes),
      plan: start?.plan ?? "undecided", plan_target: str(start?.plan_target), plan_date: str(start?.plan_date),
      plan_remind_days: str(start?.plan_remind_days), hide_upcoming: !!start?.hide_upcoming,
    };
  };
  const v = $state(initial());
  // A found card's opening day is only a guess (its first transaction here): shown as such until you change it.
  // svelte-ignore state_referenced_locally
  let openedGuess = $state(!c && !!draft);
  let changed = false;
  const uid = $props.id();
  const fp = (name: string, required = false) => fieldProps(form.errors, uid, name, required);
  let box = $state<HTMLDivElement | null>(null), first = $state<HTMLInputElement | null>(null);
  onMount(() => { first?.focus(); box?.scrollIntoView({ behavior: "smooth", block: "nearest" }); });

  // Editing: each field saves on its own. Adding: everything at once, with the Add button.
  const save = (key: string) => async (f: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement) => {
    if (!c) return;
    try { await api(`/api/churning/cards/${c.id}`, { method: "POST", body: { [key]: f instanceof HTMLInputElement && f.type === "checkbox" ? f.checked : f.value } }); }
    catch (err) { form.attempted = true; throw err; }
    changed = true;
  };
  const edit = (key: string) => (c ? fromAction(autosave, () => save(key)) : null);
  // The base rate travels inside `rates` (as the "*" category), so it isn't sent twice.
  const ratesBody = () => ({ rates: ratesPayload(v.base_rate, rates.rows), portal_name: rates.portalName });
  const add = () => form.add(async () => {
    const { base_rate: _base, ...rest } = v;
    // The benefits go with the card, in one request: if one is refused, no card is added.
    await api("/api/churning/cards", { method: "POST", body: { ...rest, ...ratesBody(), benefits: ai.benefitsBody() } });
    toast(`Added ${v.product}`);
    onclose(true);
  }, box);
  // The sections, and which one a refusal is about (the server's messages name the field).
  type Key = "rates" | "bonus" | "benefits" | "plan" | "more";
  const SECTION_OF: [Key, RegExp][] = [
    ["benefits", /benefit|resets|kind is/i], ["rates", /earning|rate|points per dollar|portal|category|listed twice|what the card earns/i],
    ["bonus", /bonus|spending|months to spend|credit card account/i], ["plan", /plan|do it by|days ahead|day it was done/i],
    ["more", /family|notes|eligible|changed from/i],
  ];
  const form = new AddForm<Key>(() => validateCard(v), SECTION_OF);
  // Deleting a card takes its to-dos, earning rates and benefits with it, and none of that comes back, so it asks first.
  let asking = $state(false);
  async function remove(): Promise<boolean> {
    return act(async () => { await api(`/api/churning/cards/${c!.id}/remove`, { method: "POST" }); toast(`Deleted ${c!.product}`); onclose(true); });
  }

  // Earning rates: kept in the form and sent with the card when adding; when editing, each change saves the whole list.
  // svelte-ignore state_referenced_locally
  const start = c;   // the form is drawn afresh for each card, so what it starts with is all it needs
  const rates = $state({ rows: (start?.rates ?? []).map((r): RateRow => ({ category: r.category, multiplier: r.multiplier, portal_only: !!r.portal_only })), portalName: str(start?.portal_name) });
  async function saveRates() {
    await api(`/api/churning/cards/${c!.id}`, { method: "POST", body: ratesBody() });
    changed = true;
  }

  // "Fill in the rest" (when an OpenRouter key is set): see cardSuggest.svelte.ts.
  const canSuggest = $derived(!!app.state?.has_api_key);
  const ai = new CardSuggest({ get card() { return c; }, v, rates, open: form.open, ratesBody, saved: () => { changed = true; }, onchanged: () => onchanged() });

  const currencyName = $derived(d.currencies.find((x) => x.key === v.currency)?.name ?? "");
  const planSummary = $derived(PLAN_LABEL[v.plan as keyof typeof PLAN_LABEL] + (PLAN_ACTS.includes(v.plan) && v.plan_date ? ` by ${fullDate(v.plan_date, d.today)}` : ""));
  const moreSummary = $derived([v.family.trim() && `Family: ${v.family.trim()}`, v.changed_from && "Product change", v.eligible_on && `Bonus again from ${fullDate(v.eligible_on, d.today)}`, v.notes.trim() && "Notes"].filter(Boolean).join(" · ") || "Nothing added");
  const sameOwner = $derived(d.cards.filter((x) => x.owner === v.owner && x.id !== c?.id));
  const lbl = "flex max-w-full flex-col gap-1 text-sm";
</script>

{#snippet star()}<span aria-hidden="true" class="text-destructive"> *</span>{/snippet}

<div bind:this={box} class="mb-4 rounded-lg bg-muted/40 p-4" data-editor>
  <h3 class="font-semibold">{c ? `Edit ${c.product}` : draft ? `Add ${draft.account_name}` : "Add a card"}</h3>
  {#if draft && !c}<p class="mt-1 text-xs text-muted-foreground">Filled in from the account Runway already has. Check each field before you add it.</p>{/if}

  <div class="mt-3 flex flex-wrap items-end gap-3">
    <label class={lbl}>Whose card
      <OwnerSelect owners={d.owners} bind:value={v.owner} {@attach edit("owner")} {...fp("owner", true)} />
    </label>
    <label class={lbl}>Bank
      <NativeSelect bind:value={v.issuer} {@attach edit("issuer")} {...fp("issuer", true)}>
        {#each d.issuers as i (i.key)}<option value={i.key}>{i.name}</option>{/each}
      </NativeSelect>
    </label>
    <div class="flex min-w-48 flex-1 flex-col gap-1">
      <label class={lbl}><span>Card{@render star()}</span><Input bind:ref={first} bind:value={v.product} {@attach edit("product")} placeholder="e.g. Sapphire Preferred" {...fp("product", true)} /></label>
      <FieldNote {uid} name="product" errors={form.errors} />
    </div>
    {#if canSuggest}
      <AiButton busy={ai.suggesting} busyLabel="Asking…" label="Fill in the rest with AI" disabled={!v.product.trim()} onclick={ai.suggest}
        title="Asks an AI model about this card, searching the web unless that’s off in Settings. Only the bank and the card’s name are sent." />
      <span class="self-center text-xs text-muted-foreground" data-testid="ai-consent">Sends only the bank and card name.</span>
    {/if}
  </div>
  <div class="mt-3 flex flex-wrap items-end gap-3">
    <div class="flex flex-col gap-1">
      <label class={lbl}><span>{openedGuess ? "Opened (on or before)" : "Opened"}{@render star()}</span><Input type="date" class="w-40" bind:value={v.opened_on} {@attach edit("opened_on")} oninput={() => (openedGuess = false)} {...fp("opened_on", true)} /></label>
      <FieldNote {uid} name="opened_on" errors={form.errors} />
    </div>
    <label class={lbl}>Status
      <NativeSelect bind:value={v.status} {@attach edit("status")}>
        <option value="open">Open</option><option value="closed">Closed</option><option value="product_changed">Product changed</option>
      </NativeSelect>
    </label>
    {#if v.status !== "open"}
      <label class={lbl}>{v.status === "closed" ? "Closed on" : "Changed on"}<Input type="date" class="w-40" bind:value={v.closed_on} {@attach edit("closed_on")} /></label>
    {/if}
    <div class="flex flex-col gap-1">
      <label class={lbl}>Annual fee<Input type="number" min="0" step="1" class="w-28" bind:value={v.annual_fee} {@attach edit("annual_fee")} {@attach commas} placeholder="0" {...fp("annual_fee")} /></label>
      <FieldNote {uid} name="annual_fee" errors={form.errors} />
    </div>
    <label class="inline-flex items-center gap-2 pb-2 text-sm"><input type="checkbox" class="size-4" bind:checked={v.authorized_user} {@attach edit("authorized_user")} />Authorized user</label>
    <label class="inline-flex items-center gap-2 pb-2 text-sm"><input type="checkbox" class="size-4" bind:checked={v.business} {@attach edit("business")} />Business card</label>
  </div>

  {#if openedGuess}
    <p class="mt-1 text-xs text-warning" data-testid="opened-guess">{draft?.opened_on ? "This is a guess: the card’s first transaction in Runway, so it was opened on or before this day. Change it to the day you opened it; 5/24 and the bonus rules depend on it." : "Runway can’t tell when this card was opened: enter the day. 5/24 and the bonus rules depend on it."}</p>
  {/if}
  {#if ai.marked}
    <div class="mt-3 flex flex-wrap items-center gap-2 rounded-md border border-dashed px-3 py-2 text-sm" role="status" data-testid="ai-marked">
      <span class="text-muted-foreground">Suggested by AI, check before saving. The fields it filled are below; change anything that’s wrong.</span>
      {#if c}<Button size="sm" onclick={ai.save}>Save these</Button>{/if}
      <Button size="sm" variant="ghost" onclick={ai.discard}>Discard</Button>
      <p class="w-full text-xs text-muted-foreground" data-testid="ai-sources">
        {#if ai.sources.length}From: {#each ai.sources as u, i (u)}{i ? ", " : ""}<a href={u} target="_blank" rel="noopener noreferrer" class="underline" title={u}>{sourceHost(u)}</a>{/each}
        {:else if ai.web}It didn’t say where this came from: check it on the bank’s site.
        {:else}From the model’s memory, without a web search (Settings → Connections → AI categorization): it may be out of date.{/if}
      </p>
    </div>
  {/if}

  <Section id="rates" title="Earning rates" bind:open={form.open.rates} flagged={form.flagged === "rates"} error={form.error} summary={ratesSummary(rates.rows, currencyName, v.currency, v.earn_note, v.base_rate)}>
    <div class="flex flex-wrap items-end gap-3">
      <label class={lbl}>Earns
        <CurrencySelect {d} bind:value={v.currency} {@attach edit("currency")} />
      </label>
      <label class={`${lbl} min-w-48 flex-1`}>Rotating or other earning<Input bind:value={v.earn_note} {@attach edit("earn_note")} placeholder="e.g. 5x quarterly categories" /></label>
    </div>
    <RatesEditor {d} bind:base={v.base_rate} bind:rows={rates.rows} bind:portalName={rates.portalName} save={c ? saveRates : undefined} />
  </Section>

  <Section id="bonus" title="Sign-up bonus" bind:open={form.open.bonus} flagged={form.flagged === "bonus"} error={form.error} summary={bonusSummary(v.bonus, v.bonus_spend, v.bonus_months, v.currency, currencyName)}>
    <div class="flex flex-wrap items-end gap-3">
      <label class={lbl}>Bonus ({v.currency === "cash" ? "dollars" : "points"})<Input type="number" min="0" step="1000" class="w-32" bind:value={v.bonus} {@attach edit("bonus")} {@attach commas} /></label>
      <div class="flex flex-col gap-1"><label class={lbl}>Spend<Input type="number" min="0" step="100" class="w-28" bind:value={v.bonus_spend} {@attach edit("bonus_spend")} {@attach commas} placeholder="$" {...fp("bonus_spend")} /></label><FieldNote {uid} name="bonus_spend" errors={form.errors} /></div>
      <label class={lbl}>Within (months)<Input type="number" min="1" max="24" class="w-24" bind:value={v.bonus_months} {@attach edit("bonus_months")} /></label>
      <label class={lbl}><span>Deadline <span class="text-muted-foreground">(if different)</span></span><Input type="date" class="w-40" bind:value={v.bonus_deadline} {@attach edit("bonus_deadline")} /></label>
      <label class={lbl}>Bonus posted on<Input type="date" class="w-40" bind:value={v.bonus_earned_on} {@attach edit("bonus_earned_on")} /></label>
    </div>
    <div class="mt-3 flex flex-wrap items-end gap-3">
      <label class={lbl}>Count spending from
        <NativeSelect bind:value={v.account_id} {@attach edit("account_id")}>
          <option value="">What I enter</option>
          {#each d.accounts as a (a.id)}<option value={a.id}>{a.name}</option>{/each}
        </NativeSelect>
      </label>
      {#if !v.account_id}
        <label class={lbl}>Spent so far<Input type="number" min="0" step="10" class="w-28" bind:value={v.manual_spend} {@attach edit("manual_spend")} {@attach commas} placeholder="$" /></label>
      {/if}
    </div>
  </Section>

  <Section id="benefits" title="Benefits" bind:open={form.open.benefits} flagged={form.flagged === "benefits"} error={form.error} summary={benefitsSummary((c?.benefits.length ?? 0) + ai.benefits.length)}>
    {#if c}
      {#if ai.benefits.length}<BenefitDrafts {d} bind:rows={ai.benefits} picker={false} />{/if}
      <CardBenefits card={c} {d} {onchanged} />
    {:else}
      <BenefitDrafts {d} bind:rows={ai.benefits} />
    {/if}
  </Section>

  <Section id="plan" title="Plan" bind:open={form.open.plan} flagged={form.flagged === "plan"} error={form.error} summary={planSummary}>
    <div class="flex flex-wrap items-end gap-3">
      <label class={lbl}>What I'll do with it
        <NativeSelect bind:value={v.plan} {@attach edit("plan")}>
          {#each d.plans as p (p)}<option value={p}>{PLAN_LABEL[p]}</option>{/each}
        </NativeSelect>
      </label>
      {#if v.plan === "product_change"}
        <label class={`${lbl} min-w-48`}>Change it to<Input bind:value={v.plan_target} {@attach edit("plan_target")} placeholder="e.g. Freedom Unlimited" /></label>
      {/if}
      {#if PLAN_ACTS.includes(v.plan)}
        <label class={lbl}>By<Input type="date" class="w-40" bind:value={v.plan_date} {@attach edit("plan_date")} /></label>
        <label class={lbl}>Remind me (days ahead)<Input type="number" min="0" max="365" class="w-24" bind:value={v.plan_remind_days} {@attach edit("plan_remind_days")} /></label>
      {/if}
      <label class="inline-flex items-center gap-2 pb-2 text-sm"><input type="checkbox" class="size-4" bind:checked={v.hide_upcoming} {@attach edit("hide_upcoming")} />Don't show this card in Upcoming</label>
    </div>
    {#if PLAN_ACTS.includes(v.plan)}
      <p class="mt-1 text-xs text-muted-foreground">Left blank: the day before the next annual fee{c?.plan_due && !v.plan_date ? ` (${fullDate(c.plan_due, d.today)})` : ""}.</p>
    {/if}
    {#if c && c.plan_done_on}
      <p class="mt-2 flex items-center gap-2 text-sm">Done {fullDate(c.plan_done_on, d.today)}<Button variant="link" size="sm" class="h-auto px-0" onclick={async () => { await planUndo(c.id, onchanged); changed = true; }}>Undo</Button></p>
    {:else if c?.plan_active}
      <Button variant="outline" size="sm" class="mt-2" onclick={async () => { await planDone(c.id, onchanged); changed = true; }}>Mark the plan done</Button>
    {/if}
  </Section>

  <Section id="more" title="More details" bind:open={form.open.more} flagged={form.flagged === "more"} error={form.error} summary={moreSummary}>
    <div class="flex flex-wrap items-end gap-3">
      <label class={lbl}><span>Family <span class="text-muted-foreground">(optional)</span></span>
        <Input class="w-40" bind:value={v.family} {@attach edit("family")} placeholder="e.g. Sapphire" title="Cards whose bonuses the bank counts as one" />
      </label>
      <label class={lbl}>A product change of
        <NativeSelect bind:value={v.changed_from} {@attach edit("changed_from")}>
          <option value="">No, a new account</option>
          {#each sameOwner as x (x.id)}<option value={String(x.id)}>{x.product} ({x.opened_on.slice(0, 4)})</option>{/each}
        </NativeSelect>
      </label>
      <label class={lbl}><span>Bonus again from <span class="text-muted-foreground">(if you know better)</span></span>
        <Input type="date" class="w-40" bind:value={v.eligible_on} {@attach edit("eligible_on")} />
      </label>
      <label class={`${lbl} min-w-60 flex-1`}>Notes<Input bind:value={v.notes} {@attach edit("notes")} /></label>
    </div>
  </Section>

  <FormFooter error={form.error} sticky={!c}>
    {#if c}
      <Button size="sm" onclick={() => onclose(changed)}>Close</Button>
      <Button variant="link" size="sm" class="text-destructive" onclick={() => (asking = true)}>Delete card</Button>
    {:else}
      <Button size="sm" onclick={add} disabled={form.busy}>{form.busy ? "Adding…" : "Add"}</Button><Button variant="link" size="sm" onclick={() => onclose(false)}>Cancel</Button>
    {/if}
  </FormFooter>
</div>

{#if c}
  <ConfirmDialog bind:open={asking} title={`Delete ${c.product}?`} confirmLabel="Delete card" busyLabel="Deleting…" destructive
    description="This also deletes its to-dos, benefits and earning rates, for good." onconfirm={remove} />
{/if}
