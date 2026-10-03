<script lang="ts">
  import { commas } from "$lib/commas";
  import { api } from "$lib/api";
  import { autosave } from "$lib/autosave";
  import { Button } from "$lib/components/ui/button";
  import { ConfirmDialog } from "$lib/components/ui/confirm-dialog";
  import { Input } from "$lib/components/ui/input";
  import { NativeSelect } from "$lib/components/ui/native-select";
  import { onMount, tick } from "svelte";
  import { fromAction } from "svelte/attachments";
  import { toast } from "svelte-sonner";
  import FieldNote from "./FieldNote.svelte";
  import { fieldProps, focusFirstInvalid } from "./form";
  import FormFooter from "./FormFooter.svelte";
  import type { Benefit, Churning } from "./types";
  import { validateBenefit } from "./validate";

  // Adding a custom benefit to a card, or editing one (each field saves as you change it, as in the card's form).
  let { card, d, b, onclose }: { card: { id: number; product: string }; d: Churning; b: Benefit | null; onclose: (changed: boolean) => void } = $props();

  const str = (x: unknown) => (x == null ? "" : String(x));
  const initial = () => {
    const s = b;
    return {
      name: str(s?.name), kind: s?.kind ?? "credit", amount: str(s?.amount), period: s?.period ?? "annual", basis: s?.basis ?? "calendar",
      annual_value: str(s?.annual_value), guests: str(s?.guests), counts: s ? !!s.counts : true, remind: s ? !!s.remind : true, remind_days: str(s?.remind_days),
      expires_on: str(s?.expires_on), notes: str(s?.notes),
    };
  };
  const v = $state(initial());
  let changed = false;
  const uid = $props.id();
  // After the first Add (or a refused save), the fields that need fixing say so, each with its own note.
  let attempted = $state(false);
  const errors = $derived(attempted ? validateBenefit(v) : {});
  const fp = (name: string, required = false) => fieldProps(errors, uid, name, required);
  let first = $state<HTMLInputElement | null>(null);
  onMount(() => first?.focus());

  const save = (key: string) => async (f: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement) => {
    if (!b) return;
    try { await api(`/api/churning/benefits/${b.id}`, { method: "POST", body: { [key]: f instanceof HTMLInputElement && f.type === "checkbox" ? f.checked : f.value } }); }
    catch (err) { attempted = true; throw err; }
    changed = true;
  };
  const edit = (key: string) => (b ? fromAction(autosave, () => save(key)) : null);
  let addError = $state(""), busy = $state(false);
  let box = $state<HTMLDivElement | null>(null);
  async function add() {
    if (busy) return;
    attempted = true; addError = "";
    await tick();
    if (Object.keys(validateBenefit(v)).length) { focusFirstInvalid(box); return; }
    busy = true;
    try { await api(`/api/churning/cards/${card.id}/benefits`, { method: "POST", body: v }); toast(`Added ${v.name}`); onclose(true); }
    catch (err) { addError = (err as Error).message; }
    finally { busy = false; }
  }
  // Deleting a benefit takes its use history with it, which can't be brought back, so it asks first.
  let asking = $state(false);
  async function remove(): Promise<boolean> {
    try { await api(`/api/churning/benefits/${b!.id}/remove`, { method: "POST" }); toast(`Deleted ${b!.name}`); onclose(true); return true; }
    catch (err) { toast.error((err as Error).message); return false; }
  }
  const lbl = "flex max-w-full flex-col gap-1 text-sm";
</script>

{#snippet star()}<span aria-hidden="true" class="text-destructive"> *</span>{/snippet}

<div bind:this={box} class="my-2 rounded-lg border bg-background p-3" data-editor>
  <h5 class="text-sm font-semibold">{b ? `Edit ${b.name}` : `Add a benefit to ${card.product}`}</h5>
  <div class="mt-2 flex flex-wrap items-end gap-3">
    <div class="flex min-w-48 flex-1 flex-col gap-1">
      <label class={lbl}><span>Benefit{@render star()}</span><Input bind:ref={first} bind:value={v.name} {@attach edit("name")} placeholder="e.g. Lyft credit, Priority Pass lounges" {...fp("name", true)} /></label>
      <FieldNote {uid} name="name" {errors} />
    </div>
    <label class={lbl}>Kind
      <NativeSelect bind:value={v.kind} {@attach edit("kind")}>{#each d.benefit_kinds as k (k.key)}<option value={k.key}>{k.name}</option>{/each}</NativeSelect>
    </label>
    {#if v.kind === "access"}
      <label class={lbl}><span>Guests free <span class="text-muted-foreground">(0: just you)</span></span>
        <Input type="number" min="0" max="20" step="1" class="w-24" bind:value={v.guests} {@attach edit("guests")} placeholder="—" />
      </label>
    {/if}
    {#if v.kind === "credit"}
      <div class="flex flex-col gap-1"><label class={lbl}>Amount<Input type="number" min="0" step="5" class="w-24" bind:value={v.amount} {@attach edit("amount")} {@attach commas} placeholder="$" {...fp("amount")} /></label><FieldNote {uid} name="amount" {errors} /></div>
    {/if}
    <label class={lbl}>Resets
      <NativeSelect bind:value={v.period} {@attach edit("period")}>{#each d.benefit_periods as p (p.key)}<option value={p.key}>{p.name}</option>{/each}</NativeSelect>
    </label>
    {#if v.period !== "one_time"}
      <label class={lbl}>On the
        <NativeSelect bind:value={v.basis} {@attach edit("basis")}>{#each d.benefit_bases as x (x.key)}<option value={x.key}>{x.name}</option>{/each}</NativeSelect>
      </label>
    {:else}
      <label class={lbl}>Expires<Input type="date" class="w-40" bind:value={v.expires_on} {@attach edit("expires_on")} /></label>
    {/if}
  </div>
  <div class="mt-3 flex flex-wrap items-end gap-3">
    <label class={lbl}><span>{v.kind === "credit" ? "Worth to me a year" : "Worth a year"} <span class="text-muted-foreground">{v.kind === "credit" ? "(if not the credit's full amount)" : "(your own figure)"}</span></span>
      <Input type="number" min="0" step="10" class="w-28" bind:value={v.annual_value} {@attach edit("annual_value")} {@attach commas} placeholder="$" />
    </label>
    <label class="inline-flex items-center gap-2 pb-2 text-sm"><input type="checkbox" class="size-4" bind:checked={v.counts} {@attach edit("counts")} />Counts toward its annual fee (I'll use it)</label>
    <label class="inline-flex items-center gap-2 pb-2 text-sm"><input type="checkbox" class="size-4" bind:checked={v.remind} {@attach edit("remind")} />Remind me before it resets</label>
    {#if v.remind}
      <label class={lbl}>Days ahead<Input type="number" min="0" max="365" class="w-24" bind:value={v.remind_days} {@attach edit("remind_days")} placeholder="auto" /></label>
    {/if}
  </div>
  <label class={`${lbl} mt-3`}>Notes<Input bind:value={v.notes} {@attach edit("notes")} /></label>
  <FormFooter error={addError}>
    {#if b}
      <Button size="sm" onclick={() => onclose(changed)}>Close</Button>
      <Button variant="link" size="sm" class="text-destructive" onclick={() => (asking = true)}>Delete</Button>
    {:else}
      <Button size="sm" onclick={add} disabled={busy}>{busy ? "Adding…" : "Add"}</Button><Button variant="link" size="sm" onclick={() => onclose(false)}>Cancel</Button>
    {/if}
  </FormFooter>
</div>

{#if b}
  <ConfirmDialog bind:open={asking} title={`Delete ${b.name}?`} confirmLabel="Delete" busyLabel="Deleting…" destructive
    description="It’s deleted for good, with its history of uses." onconfirm={remove} />
{/if}
