<script lang="ts">
  import { api } from "$lib/api";
  import { autosave } from "$lib/autosave";
  import { Button } from "$lib/components/ui/button";
  import { Input } from "$lib/components/ui/input";
  import { NativeSelect } from "$lib/components/ui/native-select";
  import { commas } from "$lib/commas";
  import { onMount } from "svelte";
  import { fromAction } from "svelte/attachments";
  import { toast } from "svelte-sonner";
  import { HOME_VALUES } from "./homeValues";
  import { ASSET_KIND_LABEL, type Asset, type NetWorth } from "./types";

  // Adding an asset (Add, or Enter, saves it all at once), or editing one's details (each field saves as you change it;
  // Done redraws the page). What's missing or refused shows under its field, and Add waits for the save.
  let { a, d, kind: startKind = "home", onclose }: { a: Asset | null; d: NetWorth; kind?: string; onclose: (changed: boolean) => void } = $props();

  const start = () => a ?? ({ kind: startKind } as Partial<Asset>);
  const v = start();
  let kind = $state(v.kind ?? "home");
  let name = $state(v.name ?? ""), value = $state(""), address = $state(v.address ?? ""), auto = $state(!!v.auto_update);
  let yc = $state(v.yearly_change != null ? String(v.yearly_change) : ""), loan = $state(v.loan_account_id ?? ""), url = $state(v.url ?? "");
  let changed = false;
  let box = $state<HTMLFormElement | null>(null), nameEl = $state<HTMLInputElement | null>(null);
  onMount(() => { nameEl?.focus(); box?.scrollIntoView({ behavior: "smooth", block: "nearest" }); });

  // Editing: each field saves on its own.
  const field = (key: string) => async (f: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement) => {
    if (!a) return;
    await api(`/api/assets/${a.id}`, { method: "POST", body: { [key]: f instanceof HTMLInputElement && f.type === "checkbox" ? f.checked : f.value } });
    changed = true;
  };
  // The autosave for a field while editing (adding saves everything at once, with the Add button).
  const edit = (key: string) => (a ? fromAction(autosave, () => field(key)) : null);
  let busy = $state(false), problem = $state<string | null>(null);
  let errs = $state<{ name?: string; value?: string }>({});
  async function add() {
    if (busy) return;
    errs = { name: name.trim() ? undefined : "Give it a name", value: value.trim() ? undefined : "Enter what it’s worth today" };
    if (errs.name || errs.value) { (errs.name ? nameEl : box?.querySelector<HTMLInputElement>("[name=value]"))?.focus(); return; }
    const body = { name, kind, yearly_change: yc, loan_account_id: loan, url, address, auto_update: auto, value };
    busy = true; problem = null;
    try { await api("/api/assets", { method: "POST", body }); toast(`Added ${name}`); onclose(true); }
    catch (err) { problem = (err as Error).message; }
    finally { busy = false; }
  }
  function submit(e: SubmitEvent) {
    e.preventDefault();
    if (!a) void add();   // editing saves each field as it changes (Enter included), so there's nothing to submit
  }
  const lbl = "flex flex-col gap-1 text-sm";
</script>

{#snippet err(id: string, msg: string | undefined)}
  {#if msg}<span {id} class="text-xs text-destructive">{msg}</span>{/if}
{/snippet}

<form bind:this={box} data-editor novalidate onsubmit={submit}>
  <div class="flex flex-wrap items-end gap-3">
    <label class={lbl}>What is it
      <NativeSelect bind:value={kind} {@attach edit("kind")}>
        {#each Object.entries(ASSET_KIND_LABEL) as [k, l] (k)}<option value={k}>{l}</option>{/each}
      </NativeSelect>
    </label>
    <div class="flex min-w-48 flex-1 flex-col gap-1">
      <label class={lbl}>Name<Input bind:ref={nameEl} bind:value={name} {@attach edit("name")} placeholder="e.g. House, 2022 Model Y"
        required aria-invalid={!!errs.name} aria-describedby={errs.name ? "asset-name-err" : undefined} oninput={() => (errs.name = undefined)} /></label>
      {@render err("asset-name-err", errs.name)}
    </div>
    {#if !a}
      <div class="flex flex-col gap-1">
        <label class={lbl}>Value today
          <span class="relative">
            <span class="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true">$</span>
            <Input type="number" name="value" min="0" step="100" class="w-40 pl-6" value={value} {@attach commas} required aria-invalid={!!errs.value}
              aria-describedby={errs.value ? "asset-value-err" : undefined} oninput={(e) => { value = e.currentTarget.value; errs.value = undefined; }} />
          </span>
        </label>
        {@render err("asset-value-err", errs.value)}
      </div>
    {/if}
  </div>
  {#if kind === "home"}
    <div class="mt-3 flex flex-wrap items-end gap-3">
      <label class={`${lbl} min-w-60 flex-1`}>Address (street, city, state, zip)<Input bind:value={address} {@attach edit("address")} /></label>
      <label class="inline-flex items-center gap-2 pb-2 text-sm">
        <input type="checkbox" class="size-4" bind:checked={auto} disabled={!d.realie.configured} {@attach edit("auto_update")} />
        <span class={d.realie.configured ? "" : "text-muted-foreground"}>{HOME_VALUES.auto}{d.realie.configured ? "" : " · needs a Realie key in Settings"}</span>
      </label>
    </div>
  {/if}
  <div class="mt-3 flex flex-wrap items-end gap-3">
    <label class={lbl}>Expected yearly change
      <span class="relative">
        <Input type="number" step="0.5" class="w-40 pr-7" value={yc} oninput={(e) => (yc = e.currentTarget.value)} {@attach edit("yearly_change")} placeholder="e.g. -15 for a car" />
        <span class="pointer-events-none absolute top-1/2 right-2.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true">%</span>
      </span>
    </label>
    <label class={lbl}>Loan against it
      <NativeSelect bind:value={loan} {@attach edit("loan_account_id")}>
        <option value="">None</option>
        {#each d.loan_accounts as l (l.id)}<option value={l.id}>{l.name}</option>{/each}
      </NativeSelect>
    </label>
    <label class={`${lbl} min-w-60 flex-1`}>Link to check the value (Zillow, KBB…)<Input bind:value={url} {@attach edit("url")} placeholder="https://" /></label>
  </div>
  {#if problem}<p class="mt-3 text-sm text-destructive" role="alert">{problem}</p>{/if}
  <div class="mt-3 flex gap-2">
    {#if a}<Button type="button" variant="link" size="sm" class="px-0" onclick={() => onclose(changed)}>Done</Button>
    {:else}<Button type="submit" size="sm" disabled={busy}>{busy ? "Adding…" : "Add"}</Button><Button type="button" variant="link" size="sm" onclick={() => onclose(false)}>Cancel</Button>{/if}
  </div>
</form>
