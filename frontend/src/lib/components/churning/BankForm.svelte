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
  import { BANK_STATUS_LABEL, BANK_TYPE_LABEL, bankFeesSummary, bankReceivedSummary, bankRequirementsSummary } from "./churning";
  import Section from "./Section.svelte";
  import type { BankBonus, Churning } from "./types";

  // Adding a bank account bonus, or editing one (each field saves as you change it; Done redraws the page).
  let { b, d, person, onclose }: { b: BankBonus | null; d: Churning; person: string; onclose: (changed: boolean) => void } = $props();

  const str = (x: unknown) => (x == null ? "" : String(x));
  // The fields as they start (the form is drawn afresh for each bonus).
  const initial = () => {
    const s = b;
    return {
      owner: s?.owner ?? (person || d.people[0] || ""), bank: str(s?.bank), account_type: s?.account_type ?? "checking",
      opened_on: str(s?.opened_on) || d.today, bonus: str(s?.bonus), account_id: str(s?.account_id), status: s?.status ?? "open",
      dd_total: str(s?.dd_total), dd_count: str(s?.dd_count), debit_count: str(s?.debit_count), min_balance: str(s?.min_balance),
      hold_until: str(s?.hold_until), other_reqs: str(s?.other_reqs), deadline_days: str(s?.deadline_days ?? 90), deadline: str(s?.deadline),
      post_days: str(s?.post_days ?? 60), manual_dd: str(s?.manual_dd), manual_debits: str(s?.manual_debits),
      received_on: str(s?.received_on), received_amount: str(s?.received_amount), closed_on: str(s?.closed_on),
      monthly_fee: str(s?.monthly_fee || ""), fee_waiver: str(s?.fee_waiver), early_close_fee: str(s?.early_close_fee),
      keep_open_days: str(s?.keep_open_days), repeat_months: str(s?.repeat_months), once_per_lifetime: !!s?.once_per_lifetime,
      eligible_on: str(s?.eligible_on), notes: str(s?.notes),
    };
  };
  const v = $state(initial());
  let changed = false;
  let box = $state<HTMLDivElement | null>(null), first = $state<HTMLInputElement | null>(null);
  onMount(() => { first?.focus(); box?.scrollIntoView({ behavior: "smooth", block: "nearest" }); });

  const save = (key: string) => async (f: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement) => {
    if (!b) return;
    await api(`/api/churning/bank/${b.id}`, { method: "POST", body: { [key]: f instanceof HTMLInputElement && f.type === "checkbox" ? f.checked : f.value } });
    changed = true;
  };
  const edit = (key: string) => (b ? fromAction(autosave, () => save(key)) : null);
  async function add() {
    try { await api("/api/churning/bank", { method: "POST", body: v }); toast(`Added ${v.bank}`); onclose(true); }
    catch (err) { toast.error((err as Error).message); }
  }
  async function remove() {
    try { await api(`/api/churning/bank/${b!.id}/remove`, { method: "POST" }); toast(`Removed ${b!.bank}`); onclose(true); }
    catch (err) { toast.error((err as Error).message); }
  }
  const lbl = "flex flex-col gap-1 text-sm";
  const open = $state({ requirements: false, fees: false, received: false });
</script>

<div bind:this={box} class="mb-4 rounded-lg bg-muted/40 p-4" data-editor>
  <h3 class="font-semibold">{b ? `Edit ${b.bank}` : "Add a bank bonus"}</h3>
  

  <div class="mt-3 flex flex-wrap items-end gap-3">
    <label class={lbl}>Whose account<OwnerSelect owners={d.owners} bind:value={v.owner} {@attach edit("owner")} /></label>
    <label class={`${lbl} min-w-40 flex-1`}>Bank<Input bind:ref={first} bind:value={v.bank} {@attach edit("bank")} placeholder="e.g. Chase" /></label>
    <label class={lbl}>Account
      <NativeSelect bind:value={v.account_type} {@attach edit("account_type")}>
        {#each Object.entries(BANK_TYPE_LABEL) as [k, l] (k)}<option value={k}>{l}</option>{/each}
      </NativeSelect>
    </label>
    <label class={lbl}>Opened<Input type="date" class="w-40" bind:value={v.opened_on} {@attach edit("opened_on")} /></label>
    <label class={lbl}>Bonus<Input type="number" min="0" step="25" class="w-28" bind:value={v.bonus} {@attach edit("bonus")} placeholder="$" /></label>
    <label class={lbl}>Status
      <NativeSelect bind:value={v.status} {@attach edit("status")}>
        {#each Object.entries(BANK_STATUS_LABEL) as [k, l] (k)}<option value={k}>{l}</option>{/each}
      </NativeSelect>
    </label>
  </div>

  <Section id="requirements" title="Requirements" bind:open={open.requirements} summary={bankRequirementsSummary(v)}>
  <div class="flex flex-wrap items-end gap-3">
    <label class={lbl}>Direct deposits total<Input type="number" min="0" step="100" class="w-32" bind:value={v.dd_total} {@attach edit("dd_total")} placeholder="$" /></label>
    <label class={lbl}>How many deposits<Input type="number" min="0" step="1" class="w-24" bind:value={v.dd_count} {@attach edit("dd_count")} /></label>
    <label class={lbl}>Debit purchases<Input type="number" min="0" step="1" class="w-24" bind:value={v.debit_count} {@attach edit("debit_count")} /></label>
    <label class={lbl}>Keep a balance of<Input type="number" min="0" step="100" class="w-32" bind:value={v.min_balance} {@attach edit("min_balance")} placeholder="$" /></label>
    <label class={lbl}>… until<Input type="date" class="w-40" bind:value={v.hold_until} {@attach edit("hold_until")} /></label>
  </div>
  <div class="mt-3 flex flex-wrap items-end gap-3">
    <label class={lbl}>Days to meet them<Input type="number" min="1" step="1" class="w-24" bind:value={v.deadline_days} {@attach edit("deadline_days")} /></label>
    <label class={lbl}><span>Deadline <span class="text-muted-foreground">(if different)</span></span><Input type="date" class="w-40" bind:value={v.deadline} {@attach edit("deadline")} /></label>
    <label class={lbl}>Posts within (days)<Input type="number" min="0" step="1" class="w-24" bind:value={v.post_days} {@attach edit("post_days")} /></label>
    <label class={`${lbl} min-w-52 flex-1`}>Anything else<Input bind:value={v.other_reqs} {@attach edit("other_reqs")} placeholder="e.g. enroll in the offer with a coupon code" /></label>
  </div>
  <div class="mt-3 flex flex-wrap items-end gap-3">
    <label class={lbl}>Follow progress from
      <NativeSelect bind:value={v.account_id} {@attach edit("account_id")}>
        <option value="">What I enter</option>
        {#each d.bank_accounts as a (a.id)}<option value={a.id}>{a.name}</option>{/each}
      </NativeSelect>
    </label>
    {#if !v.account_id}
      <label class={lbl}>Deposited so far<Input type="number" min="0" step="100" class="w-32" bind:value={v.manual_dd} {@attach edit("manual_dd")} placeholder="$" /></label>
      <label class={lbl}>Debit purchases so far<Input type="number" min="0" step="1" class="w-24" bind:value={v.manual_debits} {@attach edit("manual_debits")} /></label>
    {/if}
  </div>
  </Section>

  <Section id="fees" title="Fees & closing" bind:open={open.fees} summary={bankFeesSummary(v)}>
  <div class="flex flex-wrap items-end gap-3">
    <label class={lbl}>Monthly fee<Input type="number" min="0" step="1" class="w-24" bind:value={v.monthly_fee} {@attach edit("monthly_fee")} placeholder="0" /></label>
    <label class={`${lbl} min-w-52 flex-1`}>Waived by<Input bind:value={v.fee_waiver} {@attach edit("fee_waiver")} placeholder="e.g. $500 in direct deposits a month" /></label>
    <label class={lbl}>Early-closing fee<Input type="number" min="0" step="1" class="w-24" bind:value={v.early_close_fee} {@attach edit("early_close_fee")} placeholder="$" /></label>
    <label class={lbl}>Keep open (days)<Input type="number" min="0" step="1" class="w-24" bind:value={v.keep_open_days} {@attach edit("keep_open_days")} placeholder="e.g. 180" /></label>
  </div>
  </Section>

  <Section id="received" title="Bonus received & again" bind:open={open.received} summary={bankReceivedSummary(v)}>
  <div class="flex flex-wrap items-end gap-3">
    <label class={lbl}>Posted on<Input type="date" class="w-40" bind:value={v.received_on} {@attach edit("received_on")} /></label>
    <label class={lbl}><span>Amount <span class="text-muted-foreground">(if not the bonus)</span></span><Input type="number" min="0" step="1" class="w-28" bind:value={v.received_amount} {@attach edit("received_amount")} placeholder="$" /></label>
    <label class={lbl}>Closed on<Input type="date" class="w-40" bind:value={v.closed_on} {@attach edit("closed_on")} /></label>
    <label class={lbl}>Again after (months)<Input type="number" min="0" step="1" class="w-28" bind:value={v.repeat_months} {@attach edit("repeat_months")} placeholder="from the terms" /></label>
    <label class="inline-flex items-center gap-2 pb-2 text-sm"><input type="checkbox" class="size-4" bind:checked={v.once_per_lifetime} {@attach edit("once_per_lifetime")} />Once per lifetime</label>
    <label class={lbl}><span>Eligible again <span class="text-muted-foreground">(your date)</span></span><Input type="date" class="w-40" bind:value={v.eligible_on} {@attach edit("eligible_on")} /></label>
  </div>
  </Section>
  <label class={`${lbl} mt-3`}>Notes<Input bind:value={v.notes} {@attach edit("notes")} /></label>

  <div class="mt-4 flex flex-wrap items-center gap-2">
    {#if b}
      <Button size="sm" onclick={() => onclose(changed)}>Done</Button>
      <ConfirmButton confirm={`Remove ${b.bank}?`} onconfirm={remove}>Remove</ConfirmButton>
    {:else}
      <Button size="sm" onclick={add}>Add</Button><Button variant="link" size="sm" onclick={() => onclose(false)}>Cancel</Button>
    {/if}
  </div>
</div>
