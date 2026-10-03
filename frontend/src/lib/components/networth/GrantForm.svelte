<script lang="ts">
  import { Button } from "$lib/components/ui/button";
  import { Input } from "$lib/components/ui/input";
  import { NativeSelect } from "$lib/components/ui/native-select";
  import * as Sheet from "$lib/components/ui/sheet";
  import { commas } from "$lib/commas";
  import { onMount } from "svelte";
  import { EQ_KINDS, isOption, vestingPreview } from "./equity";
  import type { Grant, GrantBody } from "./types";

  // The body of the grant side panel (GrantSheet): what kind of stock, how many shares, and how it vests. Options also
  // have a strike price and an expiry; plain shares don't vest. A line under the vesting fields says how they read
  // ("25% on Jan 2027, then monthly until Jan 2030"). Saving waits for `onsave`, which answers with why it was refused
  // (shown under the form) or null; the shares are required.
  let { g = null, onsave, oncancel }: { g?: Grant | null; onsave: (body: GrantBody) => Promise<string | null>; oncancel: () => void } = $props();

  const s = (v: unknown) => (v == null ? "" : String(v));
  const init = (): GrantBody => {
    const x: Partial<Grant> = g ?? { kind: "iso", vest_months: 48, cliff_months: 12, vest_every: 1 };
    return {
      kind: x.kind ?? "iso", label: s(x.label), quantity: s(x.quantity), strike: s(x.strike), exercised: x.exercised ? String(x.exercised) : "",
      granted_on: s(x.granted_on), vest_start: s(x.vest_start), vest_months: s(x.vest_months), cliff_months: s(x.cliff_months),
      vest_every: String(Number(x.vest_every || 1)), expires_on: s(x.expires_on),
    };
  };
  let f = $state(init());
  const opt = $derived(isOption(f.kind));
  let qtyEl = $state<HTMLInputElement | null>(null);
  onMount(() => qtyEl?.focus());
  const lbl = "flex flex-col gap-1 text-sm";
  const num = (k: keyof GrantBody) => (e: Event & { currentTarget: HTMLInputElement }) => { f[k] = e.currentTarget.value; if (k === "quantity") qtyErr = null; };
  const preview = $derived(vestingPreview(f));

  let busy = $state(false), qtyErr = $state<string | null>(null), problem = $state<string | null>(null);
  async function submit(e: SubmitEvent) {
    e.preventDefault();
    if (busy) return;
    if (!(Number(f.quantity) > 0)) { qtyErr = "Enter how many shares"; qtyEl?.focus(); return; }
    busy = true; problem = null;
    try { problem = await onsave({ ...f }); } finally { busy = false; }
  }
</script>

<form class="flex flex-1 flex-col gap-4 px-4" novalidate onsubmit={submit}>
  <div class="grid grid-cols-2 gap-3 max-md:grid-cols-1">
    <label class={lbl}>Kind
      <NativeSelect bind:value={f.kind}>{#each Object.entries(EQ_KINDS) as [k, v] (k)}<option value={k}>{v}</option>{/each}</NativeSelect>
    </label>
    <label class={lbl}>Name<Input bind:value={f.label} placeholder="ES-12 (optional)" /></label>
    <div class="flex flex-col gap-1">
      <label class={lbl}>Shares<Input bind:ref={qtyEl} type="number" min="0" step="1" value={f.quantity} {@attach commas} oninput={num("quantity")} required
        aria-invalid={!!qtyErr} aria-describedby={qtyErr ? "grant-qty-err" : undefined} /></label>
      {#if qtyErr}<span id="grant-qty-err" class="text-xs text-destructive">{qtyErr}</span>{/if}
    </div>
    {#if opt}
      <label class={lbl}>Strike price
        <span class="relative">
          <span class="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true">$</span>
          <Input class="pl-6" type="number" min="0" step="0.0001" value={f.strike} {@attach commas} oninput={num("strike")} />
        </span>
      </label>
      <label class={lbl}>Exercised<Input type="number" min="0" step="1" value={f.exercised} {@attach commas} oninput={num("exercised")} placeholder="0" /></label>
    {/if}
    {#if f.kind !== "shares"}
      <label class={lbl}>Granted<Input type="date" bind:value={f.granted_on} /></label>
      <label class={lbl}>Vesting starts<Input type="date" bind:value={f.vest_start} /></label>
      <label class={lbl}>Vests over (months)<Input type="number" min="0" step="1" value={f.vest_months} oninput={num("vest_months")} /></label>
      <label class={lbl}>Cliff (months)<Input type="number" min="0" step="1" value={f.cliff_months} oninput={num("cliff_months")} /></label>
      <label class={lbl}>Every
        <NativeSelect bind:value={f.vest_every}>{#each [["1", "month"], ["3", "quarter"], ["12", "year"]] as [v, l] (v)}<option value={v}>{l}</option>{/each}</NativeSelect>
      </label>
      {#if opt}<label class={lbl}>Expires<Input type="date" bind:value={f.expires_on} /></label>{/if}
      {#if preview}<p class="col-span-2 text-sm text-muted-foreground max-md:col-span-1" data-testid="vesting-preview" aria-live="polite">{preview}</p>{/if}
    {/if}
  </div>
  {#if problem}<p class="text-sm text-destructive" role="alert">{problem}</p>{/if}
  <Sheet.Footer class="mt-auto px-0">
    <Button type="button" variant="outline" onclick={oncancel}>Cancel</Button>
    <Button type="submit" disabled={busy}>{busy ? "Saving…" : g ? "Save grant" : "Add grant"}</Button>
  </Sheet.Footer>
</form>
