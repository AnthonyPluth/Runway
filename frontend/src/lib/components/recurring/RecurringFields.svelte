<script lang="ts" module>
  // Shared by the recurring pages' plain inputs and selects (they carry `use:autosave`, so they can't be components).
  export const fieldCls = "h-9 w-full min-w-0 rounded-md border border-input bg-transparent px-3 py-1 text-sm text-foreground shadow-xs outline-none transition-[color,box-shadow] placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 aria-invalid:border-destructive aria-invalid:ring-destructive/30 dark:bg-input/30";
  export const selectCls = fieldCls + " cursor-pointer pl-2.5 pr-8 [&>option]:bg-popover";
</script>

<script lang="ts">
  import { autosave, markSaved } from "$lib/autosave";
  import DesktopOnly from "$lib/components/DesktopOnly.svelte";
  import { isPhone } from "$lib/phone.svelte";
  import { accountName, type Account } from "$lib/types";
  import { toast } from "svelte-sonner";
  import { tick, untrack } from "svelte";
  import { fmt0 } from "$lib/format";
  import ChevronRight from "@lucide/svelte/icons/chevron-right";
  import { FREQ_OPTIONS, MODE_OPTIONS, needsDates, signedAmount, type Errors, type RecurringValues } from "./types";

  // A recurring item's fields. With `save`, each one saves itself when you change it (the classic onEdit);
  // without it (the Add form), they just hold what you type. `errors` marks the fields to fix. `suggested` is what the
  // last few payments came to when they all missed a fixed amount (the API's suggested_amount), worked out against the
  // saved amount `suggestedFor`: offered as the new amount while the field still has that one.
  type Field = HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement;
  let { v = $bindable(), accounts, save, errors = {}, suggested = null, suggestedFor = null }: {
    v: RecurringValues; accounts: Account[]; save?: (f: Field) => Promise<void>; errors?: Errors; suggested?: number | null; suggestedFor?: number | null;
  } = $props();

  const uid = $props.id();
  const saveIf = (f: Field, fn?: (f: Field) => Promise<void>) => (fn ? autosave(f, fn) : undefined);
  // Hidden accounts aren't offered, except the one this item already uses (so it isn't silently switched).
  const options = $derived(accounts.filter((a) => !a.hidden || a.id === v.account_id));
  const dated = $derived(needsDates(v.frequency));
  const semi = $derived(v.frequency === "semimonthly");
  const lbl = "relative flex min-w-0 flex-col gap-1.5 text-sm text-muted-foreground";

  // You type the amount as a plain positive number and pick money in or out; the stored amount is signed
  // (negative for money out), which is what the forecast reads. Both are set once from the item's amount.
  let out = $state(untrack(() => !(Number(v.amount) > 0)));
  let mag = $state(untrack(() => (v.amount === null || v.amount === "" ? "" : String(Math.abs(Number(v.amount))))));
  let amountInput = $state<HTMLInputElement | null>(null);
  const push = () => { v.amount = signedAmount(mag, out); };
  async function setOut(next: boolean) {
    if (next === out) return;
    out = next; push();
    if (!save || !amountInput || v.amount === null) return;
    try { await save(amountInput); markSaved(amountInput); } catch (err) { toast.error((err as Error).message); }
  }
  // The suggestion is about the saved amount: it goes once the field has another (you took it, or typed your own) or
  // the amount comes from the payments, and comes back if a save of it fails and the old amount returns.
  const suggest = $derived.by(() => {
    if (!suggested || suggestedFor === null || v.amount_mode !== "fixed") return null;
    const n = Math.round(Math.abs(suggested)), now = Math.abs(Number(v.amount));
    return Math.abs(now - Math.abs(suggestedFor)) < 0.005 && Math.abs(now - n) > 2 ? n : null;
  });
  async function useSuggested(n: number) {
    const was = mag;
    mag = String(n); push();
    if (!save || !amountInput) return;
    await tick();   // the input shows the new amount before it saves
    try { await save(amountInput); markSaved(amountInput); }
    catch (err) { mag = was; push(); toast.error((err as Error).message); }
  }

  // Help for each field, as its tooltip.
  const hints = {
    anchor_date: "Any date it falls on works.", account_id: "The account the money moves through.",
    amount_mode: "Use the recent payments when the amount changes, like a utility bill.",
    match: "Text on the bank statement, e.g. COMED. One per line to match any of them; blank uses the name.",
    amount_max: "Leave blank to match any amount with the text.",
  };
  // While More options is closed, one line says what's in it.
  const moreSummary = $derived.by(() => {
    const acct = options.find((a) => a.id === v.account_id);
    const mode = MODE_OPTIONS.find(([val]) => val === v.amount_mode)?.[1] ?? "";
    const lines = v.match.split("\n").map((x) => x.trim()).filter(Boolean);
    const match = lines.length ? `matches ${lines[0]}${lines.length > 1 ? ` +${lines.length - 1}` : ""}` : "matches the name";
    return [acct ? accountName(acct) : "", mode.charAt(0).toLowerCase() + mode.slice(1), match].filter(Boolean).join(" · ");
  });
  let moreOpen = $state(untrack(() => !!save));   // the Add form keeps these tucked away; an existing item shows them all
  $effect(() => { if (errors.account_id || errors.amount_max) moreOpen = true; });
  const err = (name: string) => errors[name as keyof Errors];
  const ids = (name: string) => (err(name) ? `${uid}-${name}-err` : undefined);
  const bad = (name: string) => (err(name) ? "true" : undefined);
</script>

{#snippet note(name: string)}
  {#if err(name)}<p id="{uid}-{name}-err" class="text-xs text-destructive">{err(name)}</p>{/if}
{/snippet}
{#snippet star()}<span aria-hidden="true" class="text-destructive"> *</span>{/snippet}

<div class="grid gap-x-3 gap-y-4 sm:grid-cols-2 lg:grid-cols-4">
  <div class="flex min-w-0 flex-col gap-1.5">
    <label class={lbl}><span>Name{@render star()}</span>
      <input class={fieldCls} name="name" bind:value={v.name} placeholder="Paycheck" aria-required="true" aria-invalid={bad("name")} aria-describedby={ids("name")} use:saveIf={save} />
    </label>
    {@render note("name")}
  </div>
  <div class="flex min-w-0 flex-col gap-1.5 text-sm text-muted-foreground">
    <span id="{uid}-type">Type{@render star()}</span>
    <div role="radiogroup" aria-labelledby="{uid}-type" class="flex h-9 items-center gap-0.5 rounded-md bg-muted p-0.5">
      {#each [[true, "Money out"], [false, "Money in"]] as [isOut, text] (text)}
        <label class="flex h-full flex-1 cursor-pointer items-center justify-center rounded-sm px-2 text-center whitespace-nowrap transition-colors has-checked:bg-background has-checked:font-medium has-checked:text-foreground has-checked:shadow-xs has-focus-visible:ring-3 has-focus-visible:ring-ring/50">
          <input type="radio" class="sr-only" name="direction-{uid}" value={isOut ? "out" : "in"} checked={out === isOut} onchange={() => setOut(!!isOut)} />{text}
        </label>
      {/each}
    </div>
  </div>
  <div class="flex min-w-0 flex-col gap-1.5">
    <label class={lbl}><span>Amount{@render star()}</span>
      <input class={fieldCls + " tabular-nums"} name="amount" type="number" step="0.01" min="0" inputmode="decimal" bind:this={amountInput} value={mag}
        oninput={(e) => { mag = e.currentTarget.value; push(); }} placeholder="120.00" aria-required="true" aria-invalid={bad("amount")} aria-describedby={ids("amount")} use:saveIf={save} />
    </label>
    {@render note("amount")}
  </div>
  <div class="flex min-w-0 flex-col gap-1.5">
    <label class={lbl}><span>How often{@render star()}</span>
      <select class={selectCls} name="frequency" bind:value={v.frequency} use:saveIf={save}>
        {#each FREQ_OPTIONS as [val, text] (val)}<option value={val}>{text}</option>{/each}
      </select>
    </label>
  </div>
  <!-- The dates box shows only for schedules that need it, with the right hint. -->
  {#if dated}
    <div class="flex min-w-0 flex-col gap-1.5">
      <label class={lbl}><span>{semi ? "Days of the month" : "Dates each year"}{@render star()}</span>
        <input class={fieldCls} name="dates" bind:value={v.dates} placeholder={semi ? "1, 15" : "Apr 15, Oct 15"} aria-required="true" aria-invalid={bad("dates")} aria-describedby={ids("dates")} use:saveIf={save} />
      </label>
      {@render note("dates")}
    </div>
  {/if}
  <div class="flex min-w-0 flex-col gap-1.5">
    <label class={lbl}><span>{dated ? "Starting" : "Next date"}{@render star()}</span>
      <input class={fieldCls} name="anchor_date" type="date" bind:value={v.anchor_date} aria-required="true" aria-invalid={bad("anchor_date")} aria-describedby={ids("anchor_date")} title={hints.anchor_date} use:saveIf={save} />
    </label>
    {@render note("anchor_date")}
  </div>
</div>

{#if suggest}
  <p class="mt-3 flex flex-wrap items-center gap-x-2 text-sm text-muted-foreground">
    <span>The last payments were about {fmt0(suggest)}, not {fmt0(Math.abs(Number(v.amount)))}.</span>
    <button type="button" class="font-medium text-primary underline-offset-4 hover:underline" onclick={() => useSuggested(suggest)}>Use {fmt0(suggest)}</button>
  </p>
{/if}

{#if isPhone()}
  <DesktopOnly what="change the account, amount to forecast or merchant text" class="mt-4" />
{:else}
<details class="group mt-4" bind:open={moreOpen}>
  <summary class="flex w-fit max-w-full cursor-pointer list-none items-center gap-1.5 text-sm text-muted-foreground select-none hover:text-foreground [&::-webkit-details-marker]:hidden">
    <ChevronRight class="size-4 shrink-0 transition-transform group-open:rotate-90" aria-hidden="true" />
    <span class="shrink-0">More options</span>
    {#if !moreOpen}<span class="min-w-0 truncate text-xs">{moreSummary}</span>{/if}
  </summary>
  <div class="mt-3 grid gap-x-3 gap-y-4 sm:grid-cols-2 lg:grid-cols-3">
    <div class="flex min-w-0 flex-col gap-1.5">
      <label class={lbl}>Account
        <select class={selectCls} name="account_id" bind:value={v.account_id} aria-invalid={bad("account_id")} aria-describedby={ids("account_id")} title={hints.account_id} use:saveIf={save}>
          {#each options as a (a.id)}<option value={a.id}>{accountName(a)}</option>{/each}
        </select>
      </label>
      {@render note("account_id")}
    </div>
    <div class="flex min-w-0 flex-col gap-1.5">
      <label class={lbl}>Amount to forecast
        <select class={selectCls} name="amount_mode" bind:value={v.amount_mode} title={hints.amount_mode} use:saveIf={save}>
          {#each MODE_OPTIONS as [val, text] (val)}<option value={val}>{text}</option>{/each}
        </select>
      </label>
    </div>
    <div class="flex min-w-0 flex-col gap-1.5">
      <label class={lbl}>Merchant text
        <textarea class={fieldCls + " h-auto min-h-9 resize-y py-1.5"} name="match" rows={Math.max(1, v.match.split("\n").length)} bind:value={v.match}
          placeholder="Blank uses the name" title={hints.match} use:saveIf={save}></textarea>
      </label>
    </div>
    <div class="flex min-w-0 flex-col gap-1.5 text-sm text-muted-foreground">
      <span id="{uid}-range">Only amounts between</span>
      <div class="flex items-center gap-2" role="group" aria-labelledby="{uid}-range">
        <label class="relative min-w-0 flex-1"><span class="sr-only">Smallest amount</span>
          <input class={fieldCls + " tabular-nums"} name="amount_min" type="number" step="0.01" min="0" inputmode="decimal" placeholder="$ any"
            bind:value={v.amount_min} aria-describedby={ids("amount_max")} title={hints.amount_max} use:saveIf={save} />
        </label>
        <span>and</span>
        <label class="relative min-w-0 flex-1"><span class="sr-only">Largest amount</span>
          <input class={fieldCls + " tabular-nums"} name="amount_max" type="number" step="0.01" min="0" inputmode="decimal" placeholder="$ any"
            bind:value={v.amount_max} aria-invalid={bad("amount_max")} aria-describedby={ids("amount_max")} title={hints.amount_max} use:saveIf={save} />
        </label>
      </div>
      {@render note("amount_max")}
    </div>
  </div>
</details>
{/if}
