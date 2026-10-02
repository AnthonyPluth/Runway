<script lang="ts">
  import { commas } from "$lib/commas";
  import { api } from "$lib/api";
  import { app } from "$lib/app.svelte";
  import { autosave } from "$lib/autosave";
  import ConfirmButton from "$lib/components/ConfirmButton.svelte";
  import OwnerSelect from "$lib/components/OwnerSelect.svelte";
  import { Button } from "$lib/components/ui/button";
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
  import RatesEditor from "./RatesEditor.svelte";
  import Section from "./Section.svelte";
  import type { CardSuggestion, ChurnCard, Churning, DraftBenefit, FoundDraft } from "./types";

  // Adding a card, or editing one (each field saves as you change it, as elsewhere in Runway; Done redraws the page).
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
  let box = $state<HTMLDivElement | null>(null), first = $state<HTMLInputElement | null>(null);
  onMount(() => { first?.focus(); box?.scrollIntoView({ behavior: "smooth", block: "nearest" }); });

  // Editing: each field saves on its own. Adding: everything at once, with the Add button.
  const save = (key: string) => async (f: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement) => {
    if (!c) return;
    await api(`/api/churning/cards/${c.id}`, { method: "POST", body: { [key]: f instanceof HTMLInputElement && f.type === "checkbox" ? f.checked : f.value } });
    changed = true;
  };
  const edit = (key: string) => (c ? fromAction(autosave, () => save(key)) : null);
  // The base rate travels inside `rates` (as the "*" category), so it isn't sent twice.
  const ratesBody = () => ({ rates: ratesPayload(v.base_rate, rateRows), portal_name: portalName });
  let addError = $state("");
  async function add() {
    const { base_rate: _base, ...rest } = v;
    try {
      // The benefits go with the card, in one request: if one is refused, no card is added.
      await api("/api/churning/cards", { method: "POST", body: { ...rest, ...ratesBody(), benefits: benefitsBody() } });
      toast(`Added ${v.product}`);
      onclose(true);
    }
    catch (err) { addError = (err as Error).message; flagged = sectionOf(addError); if (flagged) open[flagged] = true; toast.error(addError); }
  }
  const benefitBody = ({ ai: _ai, ...b }: DraftBenefit) => ({ ...b, amount: b.amount === null || String(b.amount) === "" ? null : b.amount });
  const benefitsBody = () => aiBenefits.filter((b) => b.name.trim()).map(benefitBody);
  const saveBenefits = async (cardId: number) => {
    for (const b of benefitsBody()) await api(`/api/churning/cards/${cardId}/benefits`, { method: "POST", body: b });
  };

  // The sections, and which one a refusal is about (the server's messages name the field).
  type Key = "rates" | "bonus" | "benefits" | "plan" | "more";
  const open = $state<Record<Key, boolean>>({ rates: false, bonus: false, benefits: false, plan: false, more: false });
  let flagged = $state<Key | null>(null);
  const SECTION_OF: [Key, RegExp][] = [
    ["benefits", /benefit|resets|kind is/i], ["rates", /earning|rate|points per dollar|portal|category|listed twice|what the card earns/i],
    ["bonus", /bonus|spending|months to spend|credit card account/i], ["plan", /plan|do it by|days ahead|day it was done/i],
    ["more", /family|notes|eligible|changed from/i],
  ];
  const sectionOf = (msg: string): Key | null => SECTION_OF.find(([, re]) => re.test(msg))?.[0] ?? null;
  async function remove() {
    try { await api(`/api/churning/cards/${c!.id}/remove`, { method: "POST" }); toast(`Removed ${c!.product}`); onclose(true); }
    catch (err) { toast.error((err as Error).message); }
  }

  // Earning rates: kept in the form and sent with the card when adding; when editing, each change saves the whole list.
  // svelte-ignore state_referenced_locally
  const start = c;   // the form is drawn afresh for each card, so what it starts with is all it needs
  let rateRows = $state<RateRow[]>((start?.rates ?? []).map((r) => ({ category: r.category, multiplier: r.multiplier, portal_only: !!r.portal_only })));
  let portalName = $state(str(start?.portal_name));
  async function saveRates() {
    await api(`/api/churning/cards/${c!.id}`, { method: "POST", body: ratesBody() });
    changed = true;
  }

  // "Fill in the rest" (when an OpenRouter key is set): what the AI knows of the card from its bank and name alone. It
  // only fills what's still empty, and everything stays in the form, marked, until you save: adding saves it with the
  // card; editing asks first (Save these / Discard).
  const canSuggest = $derived(!!app.state?.has_api_key);
  let suggesting = $state(false);
  let aiMarked = $state(false);
  let aiBenefits = $state<DraftBenefit[]>([]);
  // Where the suggestions came from (web pages, http(s) only), and whether the AI searched the web at all.
  let aiSources = $state<string[]>([]);
  let aiWeb = $state(false);
  const host = (u: string) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return u; } };
  let aiUndo: (() => void) | null = null;
  let aiFields: Record<string, unknown> = {};
  let aiRates = false;
  async function suggest() {
    suggesting = true;
    try {
      applySuggestion(await api<CardSuggestion>("/api/churning/suggest", { method: "POST", body: { issuer: v.issuer, product: v.product } }));
    } catch (err) { toast.error((err as Error).message); }
    finally { suggesting = false; }
  }
  function applySuggestion(s: CardSuggestion) {
    const before: Record<string, unknown> = { ...v }, rows = rateRows.map((r) => ({ ...r })), portal = portalName, benefits = aiBenefits;
    const fields: Record<string, unknown> = {};
    if (s.family && !v.family.trim()) { v.family = s.family; fields.family = s.family; }
    if (s.currency && v.currency === "cash" && s.currency !== "cash") { v.currency = s.currency; fields.currency = s.currency; }
    if (s.annual_fee && !v.annual_fee) { v.annual_fee = String(s.annual_fee); fields.annual_fee = s.annual_fee; }
    if (s.portal_name && !portalName.trim()) { portalName = s.portal_name; fields.portal_name = s.portal_name; }
    // The public sign-up offer, only for a card you're adding (one you have came with the offer you applied with).
    if (!c && s.bonus?.amount && !v.bonus && !v.bonus_spend) {
      v.bonus = String(s.bonus.amount); fields.bonus = v.bonus;
      if (s.bonus.spend) { v.bonus_spend = String(s.bonus.spend); fields.bonus_spend = v.bonus_spend; }
      if (s.bonus.months && ["", "3"].includes(String(v.bonus_months))) { v.bonus_months = String(s.bonus.months); fields.bonus_months = v.bonus_months; }
    }
    let rates = false;
    if (!rateRows.length && (s.rates.length || s.base_rate != null)) {
      if (s.base_rate != null && ["", "1"].includes(String(v.base_rate))) { v.base_rate = String(s.base_rate); fields.base_rate = v.base_rate; }
      rateRows = s.rates.map((r) => ({ category: r.category, multiplier: r.multiplier, portal_only: !!r.portal_only }));
      rates = true;
    }
    const have = new Set([...(c?.benefits ?? []), ...aiBenefits].map((b) => b.name.toLowerCase()));   // the card's, and ones already suggested
    const fresh = s.benefits.filter((b) => !have.has(b.name.toLowerCase()));
    if (!Object.keys(fields).length && !rates && !fresh.length) { toast("The AI had nothing to add for this card"); return; }
    aiBenefits = [...aiBenefits, ...fresh.map((b) => ({ ...b, ai: true }))];
    // Sections that got something open, so the suggestions can be checked.
    if (fields.family) open.more = true;
    if (fields.currency || fields.portal_name || rates) open.rates = true;
    if (fresh.length) open.benefits = true;
    if (fields.bonus) open.bonus = true;
    aiSources = [...new Set([...aiSources, ...(s.sources ?? []).filter((u) => /^https?:\/\//i.test(u))])];
    aiWeb = !!s.web;
    aiFields = { ...aiFields, ...fields };
    aiRates = aiRates || rates;
    const prev = aiUndo;
    // Discard takes back only what the AI filled, and only where it still holds the AI's value: anything you changed
    // since (the opened date, the bonus) stays as you left it.
    const filledRows = JSON.stringify(rateRows);
    aiUndo = () => {
      const v_ = v as Record<string, unknown>;
      for (const [k, val] of Object.entries(fields)) {
        if (k === "portal_name") { if (portalName === val) portalName = portal; }
        else if (String(v_[k]) === String(val)) v_[k] = before[k];
      }
      if (rates && JSON.stringify(rateRows) === filledRows) rateRows = rows;
      aiBenefits = benefits;
      prev?.();
    };
    aiMarked = true;
  }
  function discardSuggestions() { aiUndo?.(); aiUndo = null; aiFields = {}; aiRates = false; aiMarked = false; aiSources = []; }
  async function saveSuggestions() {
    try {
      // What the form holds now for the fields the AI filled: you may have corrected one since.
      const now = Object.fromEntries(Object.keys(aiFields).map((k) => [k, k === "portal_name" ? portalName : (v as Record<string, unknown>)[k]]));
      await api(`/api/churning/cards/${c!.id}`, { method: "POST", body: { ...now, ...(aiRates ? ratesBody() : {}) } });
      await saveBenefits(c!.id);
      aiBenefits = []; aiFields = {}; aiRates = false; aiUndo = null; aiMarked = false; aiSources = []; changed = true;
      toast("Saved the suggestions");
      await onchanged();
    } catch (err) { toast.error((err as Error).message); }
  }

  const currencyName = $derived(d.currencies.find((x) => x.key === v.currency)?.name ?? "");
  const planSummary = $derived(PLAN_LABEL[v.plan as keyof typeof PLAN_LABEL] + (PLAN_ACTS.includes(v.plan) && v.plan_date ? ` by ${fullDate(v.plan_date)}` : ""));
  const moreSummary = $derived([v.family.trim() && `Family: ${v.family.trim()}`, v.changed_from && "Product change", v.eligible_on && `Bonus again from ${fullDate(v.eligible_on)}`, v.notes.trim() && "Notes"].filter(Boolean).join(" · ") || "Nothing added");
  const sameOwner = $derived(d.cards.filter((x) => x.owner === v.owner && x.id !== c?.id));
  const lbl = "flex max-w-full flex-col gap-1 text-sm";
</script>

<div bind:this={box} class="mb-4 rounded-lg bg-muted/40 p-4" data-editor>
  <h3 class="font-semibold">{c ? `Edit ${c.product}` : draft ? `Add ${draft.account_name}` : "Add a card"}</h3>
  {#if draft && !c}<p class="mt-1 text-xs text-muted-foreground">Filled in from the account Runway already has. Check each field before you add it.</p>{/if}

  <div class="mt-3 flex flex-wrap items-end gap-3">
    <label class={lbl}>Whose card
      <OwnerSelect owners={d.owners} bind:value={v.owner} {@attach edit("owner")} />
    </label>
    <label class={lbl}>Bank
      <NativeSelect bind:value={v.issuer} {@attach edit("issuer")}>
        {#each d.issuers as i (i.key)}<option value={i.key}>{i.name}</option>{/each}
      </NativeSelect>
    </label>
    <label class={`${lbl} min-w-48 flex-1`}>Card<Input bind:ref={first} bind:value={v.product} {@attach edit("product")} placeholder="e.g. Sapphire Preferred" /></label>
    {#if canSuggest}
      <Button variant="outline" size="sm" disabled={!v.product.trim() || suggesting} onclick={suggest}
        title="Asks an AI model about this card, searching the web unless that’s off in Settings. Only the bank and the card’s name are sent.">{suggesting ? "Asking…" : "Fill in the rest with AI"}</Button>
    {/if}
  </div>
  <div class="mt-3 flex flex-wrap items-end gap-3">
    <label class={lbl}>{openedGuess ? "Opened (on or before)" : "Opened"}<Input type="date" class="w-40" bind:value={v.opened_on} {@attach edit("opened_on")} oninput={() => (openedGuess = false)} /></label>
    <label class={lbl}>Status
      <NativeSelect bind:value={v.status} {@attach edit("status")}>
        <option value="open">Open</option><option value="closed">Closed</option><option value="product_changed">Product changed</option>
      </NativeSelect>
    </label>
    {#if v.status !== "open"}
      <label class={lbl}>{v.status === "closed" ? "Closed on" : "Changed on"}<Input type="date" class="w-40" bind:value={v.closed_on} {@attach edit("closed_on")} /></label>
    {/if}
    <label class={lbl}>Annual fee<Input type="number" min="0" step="1" class="w-28" bind:value={v.annual_fee} {@attach edit("annual_fee")} {@attach commas} placeholder="0" /></label>
    <label class="inline-flex items-center gap-2 pb-2 text-sm"><input type="checkbox" class="size-4" bind:checked={v.authorized_user} {@attach edit("authorized_user")} />Authorized user</label>
    <label class="inline-flex items-center gap-2 pb-2 text-sm"><input type="checkbox" class="size-4" bind:checked={v.business} {@attach edit("business")} />Business card</label>
  </div>

  {#if openedGuess}
    <p class="mt-1 text-xs text-[var(--warning)]" data-testid="opened-guess">{draft?.opened_on ? "This is a guess: the card’s first transaction in Runway, so it was opened on or before this day. Change it to the day you opened it; 5/24 and the bonus rules depend on it." : "Runway can’t tell when this card was opened: enter the day. 5/24 and the bonus rules depend on it."}</p>
  {/if}
  {#if aiMarked}
    <div class="mt-3 flex flex-wrap items-center gap-2 rounded-md border border-dashed px-3 py-2 text-sm" role="status" data-testid="ai-marked">
      <span class="text-muted-foreground">Suggested by AI, check before saving. The fields it filled are below; change anything that’s wrong.</span>
      {#if c}<Button size="sm" onclick={saveSuggestions}>Save these</Button>{/if}
      <Button size="sm" variant="ghost" onclick={discardSuggestions}>Discard</Button>
      <p class="w-full text-xs text-muted-foreground" data-testid="ai-sources">
        {#if aiSources.length}From: {#each aiSources as u, i (u)}{i ? ", " : ""}<a href={u} target="_blank" rel="noopener noreferrer" class="underline" title={u}>{host(u)}</a>{/each}
        {:else if aiWeb}It didn’t say where this came from: check it on the bank’s site.
        {:else}From the model’s memory, without a web search (Settings → Connections → AI categorization): it may be out of date.{/if}
      </p>
    </div>
  {/if}

  <Section id="rates" title="Earning rates" bind:open={open.rates} flagged={flagged === "rates"} summary={ratesSummary(rateRows, currencyName, v.currency, v.earn_note, v.base_rate)}>
    <div class="flex flex-wrap items-end gap-3">
      <label class={lbl}>Earns
        <CurrencySelect {d} bind:value={v.currency} {@attach edit("currency")} />
      </label>
      <label class={`${lbl} min-w-48 flex-1`}>Rotating or other earning<Input bind:value={v.earn_note} {@attach edit("earn_note")} placeholder="e.g. 5x quarterly categories" /></label>
    </div>
    <RatesEditor {d} bind:base={v.base_rate} bind:rows={rateRows} bind:portalName save={c ? saveRates : undefined} />
  </Section>

  <Section id="bonus" title="Sign-up bonus" bind:open={open.bonus} flagged={flagged === "bonus"} summary={bonusSummary(v.bonus, v.bonus_spend, v.bonus_months, v.currency, currencyName)}>
    <div class="flex flex-wrap items-end gap-3">
      <label class={lbl}>Bonus ({v.currency === "cash" ? "dollars" : "points"})<Input type="number" min="0" step="1000" class="w-32" bind:value={v.bonus} {@attach edit("bonus")} {@attach commas} /></label>
      <label class={lbl}>Spend<Input type="number" min="0" step="100" class="w-28" bind:value={v.bonus_spend} {@attach edit("bonus_spend")} {@attach commas} placeholder="$" /></label>
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

  <Section id="benefits" title="Benefits" bind:open={open.benefits} flagged={flagged === "benefits"} summary={benefitsSummary((c?.benefits.length ?? 0) + aiBenefits.length)}>
    {#if c}
      {#if aiBenefits.length}<BenefitDrafts {d} bind:rows={aiBenefits} picker={false} />{/if}
      <CardBenefits card={c} {d} {onchanged} />
    {:else}
      <BenefitDrafts {d} bind:rows={aiBenefits} />
    {/if}
  </Section>

  <Section id="plan" title="Plan" bind:open={open.plan} flagged={flagged === "plan"} summary={planSummary}>
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
      <p class="mt-1 text-xs text-muted-foreground">Left blank: the day before the next annual fee{c?.plan_due && !v.plan_date ? ` (${fullDate(c.plan_due)})` : ""}.</p>
    {/if}
    {#if c && c.plan_done_on}
      <p class="mt-2 flex items-center gap-2 text-sm">Done {fullDate(c.plan_done_on)}<Button variant="link" size="sm" class="h-auto px-0" onclick={async () => { await planUndo(c.id, onchanged); changed = true; }}>Undo</Button></p>
    {:else if c?.plan_active}
      <Button variant="outline" size="sm" class="mt-2" onclick={async () => { await planDone(c.id, onchanged); changed = true; }}>Mark the plan done</Button>
    {/if}
  </Section>

  <Section id="more" title="More details" bind:open={open.more} flagged={flagged === "more"} summary={moreSummary}>
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

  <div class="mt-4 flex flex-wrap items-center gap-2">
    {#if c}
      <Button size="sm" onclick={() => onclose(changed)}>Done</Button>
      <ConfirmButton confirm={`Remove ${c.product} and its to-dos?`} onconfirm={remove}>Remove card</ConfirmButton>
    {:else}
      <Button size="sm" onclick={add}>Add</Button><Button variant="link" size="sm" onclick={() => onclose(false)}>Cancel</Button>
    {/if}
  </div>
  {#if addError}<p class="mt-2 text-sm text-red-500" role="alert">{addError}</p>{/if}
</div>
