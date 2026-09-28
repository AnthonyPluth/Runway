<script lang="ts">
  import { Button } from "$lib/components/ui/button";
  import { Input } from "$lib/components/ui/input";
  import { NativeSelect } from "$lib/components/ui/native-select";
  import { onMount } from "svelte";
  import { EQ_KINDS, isOption } from "./equity";
  import type { Grant, GrantBody } from "./types";

  // A stock grant: what kind, how many shares, and how it vests. Options also have a strike price and an expiry;
  // plain shares don't vest.
  let { g = null, onsave, oncancel }: { g?: Grant | null; onsave: (body: GrantBody) => void; oncancel: () => void } = $props();

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
  const num = (k: keyof GrantBody) => (e: Event & { currentTarget: HTMLInputElement }) => { f[k] = e.currentTarget.value; };
</script>

<div class="py-3">
  <div class="flex flex-wrap items-end gap-3">
    <label class={lbl}>Kind
      <NativeSelect bind:value={f.kind}>{#each Object.entries(EQ_KINDS) as [k, v] (k)}<option value={k}>{v}</option>{/each}</NativeSelect>
    </label>
    <label class={lbl}>Name<Input class="w-44" bind:value={f.label} placeholder="ES-12 (optional)" /></label>
    <label class={lbl}>Shares<Input bind:ref={qtyEl} class="w-28" type="number" min="0" step="1" value={f.quantity} oninput={num("quantity")} /></label>
    {#if opt}
      <label class={lbl}>Strike price
        <span class="relative">
          <span class="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true">$</span>
          <Input class="w-32 pl-6" type="number" min="0" step="0.0001" value={f.strike} oninput={num("strike")} />
        </span>
      </label>
      <label class={lbl}>Exercised<Input class="w-28" type="number" min="0" step="1" value={f.exercised} oninput={num("exercised")} placeholder="0" /></label>
    {/if}
  </div>
  {#if f.kind !== "shares"}
    <div class="mt-3 flex flex-wrap items-end gap-3">
      <label class={lbl}>Granted<Input class="w-40" type="date" bind:value={f.granted_on} /></label>
      <label class={lbl}>Vesting starts<Input class="w-40" type="date" bind:value={f.vest_start} /></label>
      <label class={lbl}>Vests over (months)<Input class="w-28" type="number" min="0" step="1" value={f.vest_months} oninput={num("vest_months")} /></label>
      <label class={lbl}>Cliff (months)<Input class="w-28" type="number" min="0" step="1" value={f.cliff_months} oninput={num("cliff_months")} /></label>
      <label class={lbl}>Every
        <NativeSelect bind:value={f.vest_every}>{#each [["1", "month"], ["3", "quarter"], ["12", "year"]] as [v, l] (v)}<option value={v}>{l}</option>{/each}</NativeSelect>
      </label>
      {#if opt}<label class={lbl}>Expires<Input class="w-40" type="date" bind:value={f.expires_on} /></label>{/if}
    </div>
  {/if}
  <div class="mt-3 flex gap-2">
    <Button size="sm" onclick={() => onsave({ ...f })}>{g ? "Save grant" : "Add grant"}</Button>
    <Button variant="link" size="sm" onclick={oncancel}>Cancel</Button>
  </div>
</div>
