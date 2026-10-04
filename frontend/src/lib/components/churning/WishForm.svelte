<script lang="ts">
  import { commas } from "$lib/commas";
  import { api } from "$lib/api";
  import { autosave } from "$lib/autosave";
  import OwnerSelect from "$lib/components/OwnerSelect.svelte";
  import { Button } from "$lib/components/ui/button";
  import { ConfirmDialog } from "$lib/components/ui/confirm-dialog";
  import { Input } from "$lib/components/ui/input";
  import { NativeSelect } from "$lib/components/ui/native-select";
  import { onMount } from "svelte";
  import { fromAction } from "svelte/attachments";
  import { toast } from "svelte-sonner";
  import { BANK_TYPE_LABEL, wishExpectSummary, wishName, wishTimingSummary } from "./churning";
  import CurrencySelect from "./CurrencySelect.svelte";
  import FieldNote from "./FieldNote.svelte";
  import { fieldProps } from "./form";
  import FormFooter from "./FormFooter.svelte";
  import Section from "./Section.svelte";
  import type { Churning, Wish } from "./types";
  import { validateWish } from "./validate";
  import { act } from "$lib/act";
  import { AddForm } from "./addForm.svelte";

  // Planning a card or a bank bonus: what you expect it to cost and pay, and what you're waiting for. Adding sends it
  // all with Add; editing saves each field as you change it (Close redraws the page).
  let { w, d, person, onclose }: { w: Wish | null; d: Churning; person: string; onclose: (changed: boolean) => void } = $props();

  const str = (x: unknown) => (x == null ? "" : String(x));
  const initial = () => {
    const s = w;
    return {
      owner: s?.owner ?? (person || d.people[0] || ""), kind: s?.kind ?? "card", issuer: s?.issuer ?? "chase", product: str(s?.product),
      bank: str(s?.bank), family: str(s?.family), business: !!s?.business, annual_fee: str(s?.annual_fee), bonus: str(s?.bonus),
      currency: s?.currency ?? "cash", bonus_spend: str(s?.bonus_spend), bonus_months: str(s?.bonus_months),
      account_type: s?.account_type ?? "checking", requirements: str(s?.requirements), repeat_months: str(s?.repeat_months),
      once_per_lifetime: !!s?.once_per_lifetime, offer_expires_on: str(s?.offer_expires_on), wait_until: str(s?.wait_until),
      min_score: str(s?.min_score), assume_prior_planned: !!s?.assume_prior_planned, notes: str(s?.notes), apply_url: str(s?.apply_url),
      status: s?.status === "dropped" ? "dropped" : "wanted",
    };
  };
  const v = $state(initial());
  let changed = false;
  const uid = $props.id();
  const fp = (name: string, required = false) => fieldProps(form.errors, uid, name, required);
  let box = $state<HTMLDivElement | null>(null), first = $state<HTMLInputElement | null>(null);
  onMount(() => { first?.focus(); box?.scrollIntoView({ behavior: "smooth", block: "nearest" }); });

  const save = (key: string) => async (f: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement) => {
    if (!w) return;
    try { await api(`/api/churning/wishlist/${w.id}`, { method: "POST", body: { [key]: f instanceof HTMLInputElement && f.type === "checkbox" ? f.checked : f.value } }); }
    catch (err) { form.attempted = true; throw err; }
    changed = true;
  };
  const edit = (key: string) => (w ? fromAction(autosave, () => save(key)) : null);
  // Only the fields that belong to the kind go.
  const CARD = ["issuer", "product", "family", "business", "annual_fee", "currency", "bonus_spend", "bonus_months"];
  const BANK = ["bank", "product", "account_type", "requirements", "repeat_months", "once_per_lifetime"];

  // Which section a refusal is about (the server's messages name the field), so that section opens.
  type Key = "expect" | "timing";
  const SECTION_OF: [Key, RegExp][] = [
    ["timing", /day the offer ends|day to wait until|credit score|offer|wait/i],
    ["expect", /annual fee|bonus|spending needed|months|requirements|paid in|between bonuses/i],
  ];
  const form = new AddForm<Key>(() => validateWish(v), SECTION_OF);
  const add = () => form.add(async () => {
    const own = v.kind === "card" ? CARD : BANK;
    const body: Record<string, unknown> = { owner: v.owner, kind: v.kind, bonus: v.bonus, offer_expires_on: v.offer_expires_on, wait_until: v.wait_until,
      min_score: v.min_score, assume_prior_planned: v.assume_prior_planned, notes: v.notes, apply_url: v.apply_url };
    for (const k of own) body[k] = v[k as keyof typeof v];
    await api("/api/churning/wishlist", { method: "POST", body });
    toast(`Planned ${wishName({ ...v, kind: v.kind as Wish["kind"] })}`);
    onclose(true);
  }, box);
  // Deleting a planned item can't be taken back, so it asks first.
  let asking = $state(false);
  async function remove(): Promise<boolean> {
    return act(async () => { await api(`/api/churning/wishlist/${w!.id}/remove`, { method: "POST" }); toast(`Deleted ${wishName(w!)}`); onclose(true); });
  }
  const lbl = "flex max-w-full flex-col gap-1 text-sm";
</script>

{#snippet star()}<span aria-hidden="true" class="text-destructive"> *</span>{/snippet}

<div bind:this={box} class="mb-4 rounded-lg bg-muted/40 p-4" data-editor>
  <h3 class="font-semibold">{w ? `Edit ${wishName(w)}` : "Plan a card or bank bonus"}</h3>

  <div class="mt-3 flex flex-wrap items-end gap-3">
    {#if !w}
      <label class={lbl}>What
        <NativeSelect bind:value={v.kind}><option value="card">A credit card</option><option value="bank_bonus">A bank account bonus</option></NativeSelect>
      </label>
    {/if}
    <label class={lbl}>{v.kind === "card" ? "Whose card" : "Whose account"}
      <OwnerSelect owners={d.owners} bind:value={v.owner} {@attach edit("owner")} />
    </label>
    {#if v.kind === "card"}
      <label class={lbl}>Bank
        <NativeSelect bind:value={v.issuer} {@attach edit("issuer")}>{#each d.issuers as i (i.key)}<option value={i.key}>{i.name}</option>{/each}</NativeSelect>
      </label>
      <div class="flex min-w-48 flex-1 flex-col gap-1">
        <label class={lbl}><span>Card{@render star()}</span><Input bind:ref={first} bind:value={v.product} {@attach edit("product")} placeholder="e.g. Sapphire Preferred" {...fp("product", true)} /></label>
        <FieldNote {uid} name="product" errors={form.errors} />
      </div>
      <label class={lbl}><span>Family <span class="text-muted-foreground">(optional)</span></span><Input class="w-40" bind:value={v.family} {@attach edit("family")} placeholder="e.g. Sapphire" /></label>
    {:else}
      <div class="flex min-w-40 flex-1 flex-col gap-1">
        <label class={lbl}><span>Bank{@render star()}</span><Input bind:ref={first} bind:value={v.bank} {@attach edit("bank")} placeholder="e.g. Chase" {...fp("bank", true)} /></label>
        <FieldNote {uid} name="bank" errors={form.errors} />
      </div>
      <label class={lbl}>Account
        <NativeSelect bind:value={v.account_type} {@attach edit("account_type")}>{#each Object.entries(BANK_TYPE_LABEL) as [k, l] (k)}<option value={k}>{l}</option>{/each}</NativeSelect>
      </label>
      <label class={lbl}><span>Offer <span class="text-muted-foreground">(optional)</span></span><Input class="w-44" bind:value={v.product} {@attach edit("product")} placeholder="e.g. Total Checking" /></label>
    {/if}
  </div>

  <Section id="expect" title="What you expect" bind:open={form.open.expect} flagged={form.flagged === "expect"} error={form.error} summary={wishExpectSummary(v, d.currencies.find((c) => c.key === v.currency)?.name ?? "")}>
  <div class="flex flex-wrap items-end gap-3">
    {#if v.kind === "card"}
      <label class={lbl}>Annual fee<Input type="number" min="0" step="1" class="w-28" bind:value={v.annual_fee} {@attach edit("annual_fee")} {@attach commas} placeholder="$" /></label>
      <label class={lbl}>Bonus is paid in<CurrencySelect {d} bind:value={v.currency} {@attach edit("currency")} /></label>
    {/if}
    <label class={lbl}>Bonus ({v.kind === "card" && v.currency !== "cash" ? "points" : "dollars"})<Input type="number" min="0" step={v.kind === "card" ? 1000 : 25} class="w-32" bind:value={v.bonus} {@attach edit("bonus")} {@attach commas} /></label>
    {#if v.kind === "card"}
      <label class={lbl}>Spend<Input type="number" min="0" step="100" class="w-28" bind:value={v.bonus_spend} {@attach edit("bonus_spend")} {@attach commas} placeholder="$" /></label>
      <label class={lbl}>Within (months)<Input type="number" min="1" max="24" class="w-24" bind:value={v.bonus_months} {@attach edit("bonus_months")} /></label>
      <label class="inline-flex items-center gap-2 pb-2 text-sm"><input type="checkbox" class="size-4" bind:checked={v.business} {@attach edit("business")} />Business card (doesn't count toward 5/24)</label>
    {:else}
      <label class={`${lbl} min-w-52 flex-1`}>Requirements<Input bind:value={v.requirements} {@attach edit("requirements")} placeholder="e.g. $2,000 in direct deposits in 90 days" /></label>
      <label class={lbl}>Again after (months)<Input type="number" min="0" step="1" class="w-28" bind:value={v.repeat_months} {@attach edit("repeat_months")} /></label>
      <label class="inline-flex items-center gap-2 pb-2 text-sm"><input type="checkbox" class="size-4" bind:checked={v.once_per_lifetime} {@attach edit("once_per_lifetime")} />Once per lifetime</label>
    {/if}
  </div>
  </Section>

  <Section id="timing" title="Timing" bind:open={form.open.timing} flagged={form.flagged === "timing"} error={form.error} summary={wishTimingSummary(v, d.today)}>
  <div class="flex flex-wrap items-end gap-3">
    <label class={lbl}><span>Offer ends <span class="text-muted-foreground">(optional)</span></span><Input type="date" class="w-40" bind:value={v.offer_expires_on} {@attach edit("offer_expires_on")} /></label>
    <label class={lbl}><span>Don't apply before <span class="text-muted-foreground">(optional)</span></span><Input type="date" class="w-40" bind:value={v.wait_until} {@attach edit("wait_until")} /></label>
    <label class={lbl}><span>Credit score wanted <span class="text-muted-foreground">(optional)</span></span><Input type="number" min="300" max="900" step="1" class="w-28" bind:value={v.min_score} {@attach edit("min_score")} placeholder="e.g. 740" /></label>
    {#if v.kind === "card"}
      <label class="inline-flex items-center gap-2 pb-2 text-sm"><input type="checkbox" class="size-4" bind:checked={v.assume_prior_planned} {@attach edit("assume_prior_planned")} />Count my planned cards ahead of it as opened</label>
    {/if}
    {#if w}
      <label class={lbl}>Status
        <NativeSelect bind:value={v.status} {@attach edit("status")}><option value="wanted">Wanted</option><option value="dropped">Dropped</option></NativeSelect>
      </label>
    {/if}
  </div>
  </Section>
  <label class={`${lbl} mt-3`}><span>Application link <span class="text-muted-foreground">(optional)</span></span><Input type="url" bind:value={v.apply_url} {@attach edit("apply_url")} placeholder="https://…" /></label>
  <label class={`${lbl} mt-3`}>Notes<Input bind:value={v.notes} {@attach edit("notes")} /></label>

  <FormFooter error={form.error} sticky={!w}>
    {#if w}
      <Button size="sm" onclick={() => onclose(changed)}>Close</Button>
      <Button variant="link" size="sm" class="text-destructive" onclick={() => (asking = true)}>Delete</Button>
    {:else}
      <Button size="sm" onclick={add} disabled={form.busy}>{form.busy ? "Adding…" : "Add"}</Button><Button variant="link" size="sm" onclick={() => onclose(false)}>Cancel</Button>
    {/if}
  </FormFooter>
</div>

{#if w}
  <ConfirmDialog bind:open={asking} title={`Delete ${wishName(w)}?`} confirmLabel="Delete" busyLabel="Deleting…" destructive
    description="It’s deleted for good." onconfirm={remove} />
{/if}
