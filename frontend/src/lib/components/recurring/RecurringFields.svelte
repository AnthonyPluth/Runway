<script lang="ts" module>
  // Shared by the recurring pages' plain inputs and selects (they carry `use:autosave`, so they can't be components).
  export const fieldCls = "h-9 w-full min-w-0 rounded-md border border-input bg-transparent px-3 py-1 text-sm text-foreground shadow-xs outline-none transition-[color,box-shadow] placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30";
  export const selectCls = fieldCls + " cursor-pointer pl-2.5 pr-8 [&>option]:bg-popover";
</script>

<script lang="ts">
  import { autosave } from "$lib/autosave";
  import { accountName, type Account } from "$lib/types";
  import { FREQ_OPTIONS, MODE_OPTIONS, needsDates, type RecurringValues } from "./types";

  // A recurring item's fields. With `save`, each one saves itself when you change it (the classic onEdit);
  // without it (the Add form), they just hold what you type.
  type Field = HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement;
  let { v = $bindable(), accounts, save }: { v: RecurringValues; accounts: Account[]; save?: (f: Field) => Promise<void> } = $props();

  const saveIf = (f: Field, fn?: (f: Field) => Promise<void>) => (fn ? autosave(f, fn) : undefined);
  // Hidden accounts aren't offered, except the one this item already uses (so it isn't silently switched).
  const options = $derived(accounts.filter((a) => !a.hidden || a.id === v.account_id));
  const dated = $derived(needsDates(v.frequency));
  const semi = $derived(v.frequency === "semimonthly");
  const lbl = "relative flex min-w-0 flex-col gap-1.5 text-sm text-muted-foreground";
</script>

<div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
  <label class={lbl}>Name<input class={fieldCls} name="name" bind:value={v.name} placeholder="Paycheck" use:saveIf={save} /></label>
  <label class={lbl}>Account
    <select class={selectCls} name="account_id" bind:value={v.account_id} use:saveIf={save}>
      {#each options as a (a.id)}<option value={a.id}>{accountName(a)}</option>{/each}
    </select>
  </label>
  <label class={lbl}>Amount<input class={fieldCls + " tabular-nums"} name="amount" type="number" step="0.01" bind:value={v.amount} placeholder="-120.00" use:saveIf={save} /></label>
  <label class={lbl}>Forecast amount
    <select class={selectCls} name="amount_mode" bind:value={v.amount_mode} use:saveIf={save}>
      {#each MODE_OPTIONS as [val, text] (val)}<option value={val}>{text}</option>{/each}
    </select>
  </label>
  <label class={lbl}>How often
    <select class={selectCls} name="frequency" bind:value={v.frequency} use:saveIf={save}>
      {#each FREQ_OPTIONS as [val, text] (val)}<option value={val}>{text}</option>{/each}
    </select>
  </label>
  <!-- The dates box shows only for schedules that need it, with the right hint. -->
  <label class={dated ? lbl : "hidden"}>{semi ? "Days of the month" : "Dates each year"}
    <input class={fieldCls} name="dates" bind:value={v.dates} placeholder={semi ? "1, 15" : "Apr 15, Oct 15"} use:saveIf={save} />
  </label>
  <label class={lbl}>{dated ? "Starting" : "A date it happens"}<input class={fieldCls} name="anchor_date" type="date" bind:value={v.anchor_date} use:saveIf={save} /></label>
  <label class={lbl}>Merchant text<input class={fieldCls} name="match" bind:value={v.match} placeholder="e.g. comed" use:saveIf={save} /></label>
</div>
