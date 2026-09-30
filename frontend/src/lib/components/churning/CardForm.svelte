<script lang="ts">
  import { api } from "$lib/api";
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
  import CardBenefits from "./CardBenefits.svelte";
  import { MONTHS, PLAN_ACTS, PLAN_LABEL, fullDate, ratesPayload, type RateRow } from "./churning";
  import CurrencySelect from "./CurrencySelect.svelte";
  import RatesEditor from "./RatesEditor.svelte";
  import type { ChurnCard, Churning } from "./types";

  // Adding a card, or editing one (each field saves as you change it, as elsewhere in Runway; Done redraws the page).
  // `onchanged` redraws the page's data without closing the form (a benefit was used, a plan checked off).
  let { c, d, person, onclose, onchanged }: {
    c: ChurnCard | null; d: Churning; person: string; onclose: (changed: boolean) => void; onchanged: () => void | Promise<void>;
  } = $props();

  const str = (x: unknown) => (x == null ? "" : String(x));
  // The fields as they start (the form is drawn afresh for each card).
  const initial = () => {
    const start = c;
    return {
      owner: start?.owner ?? (person || d.people[0] || ""), issuer: start?.issuer ?? "chase", product: str(start?.product),
      family: str(start?.family), opened_on: str(start?.opened_on) || d.today, status: start?.status ?? "open",
      closed_on: str(start?.closed_on), changed_from: str(start?.changed_from), authorized_user: !!start?.authorized_user,
      business: !!start?.business, annual_fee: str(start?.annual_fee || ""), fee_month: str(start?.fee_month),
      currency: start?.currency ?? "cash", base_rate: str(start?.base_rate ?? 1), earn_note: str(start?.earn_note),
      account_id: str(start?.account_id), bonus: str(start?.bonus), bonus_spend: str(start?.bonus_spend),
      bonus_months: str(start?.bonus_months ?? 3), bonus_deadline: str(start?.bonus_deadline), manual_spend: str(start?.manual_spend),
      bonus_earned_on: str(start?.bonus_earned_on), eligible_on: str(start?.eligible_on), notes: str(start?.notes),
      plan: start?.plan ?? "undecided", plan_target: str(start?.plan_target), plan_date: str(start?.plan_date),
      plan_remind_days: str(start?.plan_remind_days), hide_upcoming: !!start?.hide_upcoming,
    };
  };
  const v = $state(initial());
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
    try { await api("/api/churning/cards", { method: "POST", body: { ...rest, ...ratesBody() } }); toast(`Added ${v.product}`); onclose(true); }
    catch (err) { addError = (err as Error).message; toast.error(addError); }
  }
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

  const sameOwner = $derived(d.cards.filter((x) => x.owner === v.owner && x.id !== c?.id));
  const lbl = "flex max-w-full flex-col gap-1 text-sm";
  const h = "mt-4 mb-2 text-xs font-medium tracking-wide text-muted-foreground uppercase";
</script>

<div bind:this={box} class="mb-4 rounded-lg bg-muted/40 p-4" data-editor>
  <h3 class="font-semibold">{c ? `Edit ${c.product}` : "Add a card"}</h3>

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
    <label class={lbl}><span>Family <span class="text-muted-foreground">(optional)</span></span>
      <Input class="w-40" bind:value={v.family} {@attach edit("family")} placeholder="e.g. Sapphire" title="Cards whose bonuses the bank counts as one" />
    </label>
  </div>
  <div class="mt-3 flex flex-wrap items-end gap-3">
    <label class={lbl}>Opened<Input type="date" class="w-40" bind:value={v.opened_on} {@attach edit("opened_on")} /></label>
    <label class={lbl}>Status
      <NativeSelect bind:value={v.status} {@attach edit("status")}>
        <option value="open">Open</option><option value="closed">Closed</option><option value="product_changed">Product changed</option>
      </NativeSelect>
    </label>
    {#if v.status !== "open"}
      <label class={lbl}>{v.status === "closed" ? "Closed on" : "Changed on"}<Input type="date" class="w-40" bind:value={v.closed_on} {@attach edit("closed_on")} /></label>
    {/if}
    <label class="inline-flex items-center gap-2 pb-2 text-sm"><input type="checkbox" class="size-4" bind:checked={v.authorized_user} {@attach edit("authorized_user")} />Authorized user</label>
    <label class="inline-flex items-center gap-2 pb-2 text-sm"><input type="checkbox" class="size-4" bind:checked={v.business} {@attach edit("business")} />Business card</label>
  </div>

  <h4 class={h}>Annual fee and earning</h4>
  <div class="flex flex-wrap items-end gap-3">
    <label class={lbl}>Annual fee<Input type="number" min="0" step="1" class="w-28" bind:value={v.annual_fee} {@attach edit("annual_fee")} placeholder="0" /></label>
    <label class={lbl}>Fee posts in
      <NativeSelect bind:value={v.fee_month} {@attach edit("fee_month")}>
        <option value="">Its anniversary month</option>
        {#each MONTHS as m, i (m)}<option value={String(i + 1)}>{m}</option>{/each}
      </NativeSelect>
    </label>
    <label class={lbl}>Earns
      <CurrencySelect {d} bind:value={v.currency} {@attach edit("currency")} />
    </label>
    <label class={`${lbl} min-w-48 flex-1`}>Rotating or other earning<Input bind:value={v.earn_note} {@attach edit("earn_note")} placeholder="e.g. 5x quarterly categories" /></label>
  </div>
  <RatesEditor {d} bind:base={v.base_rate} bind:rows={rateRows} bind:portalName save={c ? saveRates : undefined} />

  <h4 class={h}>Sign-up bonus</h4>
  <div class="flex flex-wrap items-end gap-3">
    <label class={lbl}>Bonus ({v.currency === "cash" ? "dollars" : "points"})<Input type="number" min="0" step="1000" class="w-32" bind:value={v.bonus} {@attach edit("bonus")} /></label>
    <label class={lbl}>Spend<Input type="number" min="0" step="100" class="w-28" bind:value={v.bonus_spend} {@attach edit("bonus_spend")} placeholder="$" /></label>
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
      <label class={lbl}>Spent so far<Input type="number" min="0" step="10" class="w-28" bind:value={v.manual_spend} {@attach edit("manual_spend")} placeholder="$" /></label>
    {/if}
  </div>

  <h4 class={h}>Plan</h4>
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
    <p class="mt-1 text-xs text-muted-foreground">Left blank, the day is the day before the next annual fee posts{c?.plan_due && !v.plan_date ? ` (${fullDate(c.plan_due)})` : ""}. Keeping a card, or hiding it here, stops its fee reminders.</p>
  {:else if v.plan === "keep"}
    <p class="mt-1 text-xs text-muted-foreground">Keeping it: no annual-fee reminder for this card.</p>
  {/if}
  {#if c && c.plan_done_on}
    <p class="mt-2 flex items-center gap-2 text-sm">Done {fullDate(c.plan_done_on)}<Button variant="link" size="sm" class="h-auto px-0" onclick={async () => { await planUndo(c.id, onchanged); changed = true; }}>Undo</Button></p>
  {:else if c?.plan_active}
    <Button variant="outline" size="sm" class="mt-2" onclick={async () => { await planDone(c.id, onchanged); changed = true; }}>Mark the plan done</Button>
  {/if}

  <h4 class={h}>Benefits</h4>
  {#if c}
    <CardBenefits card={c} {d} {onchanged} />
  {:else}
    <p class="text-sm text-muted-foreground">Add the card first, then its benefits (lounge access, travel and hotel credits) from Edit.</p>
  {/if}

  <h4 class={h}>More</h4>
  <div class="flex flex-wrap items-end gap-3">
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
