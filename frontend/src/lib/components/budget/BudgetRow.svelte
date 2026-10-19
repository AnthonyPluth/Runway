<script lang="ts">
  import { apiCall } from "$lib/contract";
  import { app } from "$lib/app.svelte";
  import { catLook } from "$lib/categories.svelte";
  import BankBadge from "$lib/components/BankBadge.svelte";
  import CatIcon from "$lib/components/CatIcon.svelte";
  import { Button } from "$lib/components/ui/button";
  import { showTransactions } from "$lib/filters.svelte";
  import * as Popover from "$lib/components/ui/popover";
  import * as Sheet from "$lib/components/ui/sheet";
  import { Segmented } from "$lib/components/ui/toggle-group";
  import { barWidth, fmt, fmt0, monthLabel, monthShort } from "$lib/format";
  import { viewport } from "$lib/phone.svelte";
  import Repeat from "@lucide/svelte/icons/repeat";
  import Pencil from "@lucide/svelte/icons/pencil";
  import Ellipsis from "@lucide/svelte/icons/ellipsis";
  import { cn } from "$lib/utils";
  import { commas } from "$lib/commas";
  import { toast } from "svelte-sonner";
  import type { BudgetCategory, PayAccount } from "./types";
  import { act } from "$lib/act";
  import { undoable } from "$lib/undo";

  // One line per category: its name, spent "of" its budget (a button that opens its editor), then the bar underneath (a
  // top-level category's only: a subcategory's figures turn red when it's over). `budgets` is true in the Budgets card,
  // where the budget's button is always shown. `account` is the card or account its spending goes on (its own, else its parent's,
  // else the one used most; chosen in Settings → Categories): its bank's logo sits on the category's emoji. What's still
  // expected this month (recurring payments that haven't come yet, `expected`) is the bar's lighter part after what's spent.
  // `income`: an income category, whose budget is what's expected to come in and `spent` what has. More is good: its
  // bar is green, going over isn't a warning, and it has no rollover or card. Whether a budget's left rolls over is
  // switched in the row's ⋯ menu; a blue "Rolls over" label on the row only says when it's on and isn't a button.
  // `locked`: the page isn't sure these figures are the month it's showing (another month is loading, or a reload
  // failed), so every control that changes a budget is off until it is.
  // `onchanged` reloads the page after a change (and after a failed reset, so the row shows what's saved).
  let { c, month, sub = false, budgets = false, income = false, pace, payAccounts, account = null, locked = false, onchanged }: {
    c: BudgetCategory; month: string; sub?: boolean; budgets?: boolean; income?: boolean; pace: number; payAccounts: PayAccount[];
    account?: string | null; locked?: boolean; onchanged: () => void;
  } = $props();

  const brand = $derived(account ? app.state?.brands?.[account] : undefined);
  const accountName = $derived(account ? payAccounts.find((x) => x.id === account)?.name ?? brand?.institution ?? undefined : undefined);
  const avail = $derived(c.available ?? c.budget);
  const pct = $derived(avail != null && avail > 0 ? Math.max(0, c.spent / avail) : c.spent > 0 ? 1 : 0);
  const over = $derived(avail != null && c.spent > avail);
  const expected = $derived(c.expected ?? 0);
  const coming = $derived(expected > 0.005);
  // Over once what's still expected comes (and not over already).
  const overSoon = $derived(!income && !over && coming && avail != null && c.spent + expected > avail + 0.005);
  const expShare = $derived(avail != null && avail > 0 ? expected / avail : coming ? 1 : 0);
  const carried = $derived(c.carried ?? 0);
  const showPace = $derived(pace > 0 && pace < 1);
  const color = $derived(income ? "var(--good)" : catLook(c.name).color);
  const warnOver = $derived(over && !income);   // spending over its budget is red; income over what's expected isn't
  // Whole dollars, like the rest of the page; cents only when rounding would make a small amount read as $0.
  const money = (v: number | null) => (v != null && Math.abs(v) >= 0.005 && Math.abs(v) < 0.5 ? fmt(v) : fmt0(v));
  // A subcategory has no bar: its figure turns red when it's over (bold), or will be with what's still coming.
  const subNote = $derived(!sub || income ? "" : over ? `${money(c.spent - avail!)} over` : overSoon ? `${money(c.spent + expected - avail!)} over with what’s coming` : "");

  // Rollover's on/off lives in the row's ⋯ menu; the row itself only says "Rolls over" (blue) when the budget's left
  // carries into next month. An income category and a subcategory have no rollover.
  let menu = $state(false);
  const canRoll = $derived(budgets && c.budget != null && !sub && !income);

  // The category name and its spent amount open Transactions showing exactly what adds up to it (and what's still
  // expected, in its Upcoming).
  function open(e: MouseEvent) {
    e.preventDefault();
    showTransactions({ category: c.name, month, scope: "budget" });
  }

  // A month of its own: a budget's amount for the month on screen only (December's gifts), every other month keeping
  // the usual one (`usual_budget`). The amount is a button that opens its editor (a popover; a sheet from the bottom on a
  // phone): the amount and, in the Budgets card once there's a budget, which months it's for ("This month only" or
  // "Usual monthly amount"), with what that means beside it. Nothing is sent until Save; Cancel (or Escape, or a click
  // away) sends nothing. Amounts mean what POST /api/budget says: as the usual amount, an empty box or 0 removes the
  // budget; for the month, an empty box takes it back to the usual amount and 0 budgets nothing that month.
  const ownMonth = $derived(c.month_budget != null);
  const usual = $derived(c.usual_budget ?? c.budget);
  const monthName = $derived(monthShort(month, true));
  const canMonth = $derived(budgets && c.budget != null);   // a month's own amount needs a budget (the API says so too)

  const uid = $props.id();
  const title = $derived(`Edit ${c.name} budget`);
  const fieldName = $derived(`${title}: ${c.budget != null ? money(c.budget) : "not set"}`
    + (c.budget == null ? "" : canMonth && ownMonth ? `, ${monthName} only` : ", usual monthly amount"));
  const fieldCls = "inline-flex h-9 min-w-[5.25rem] cursor-pointer items-center justify-between gap-1.5 rounded-md border border-input bg-transparent px-2 text-sm tabular-nums outline-none hover:bg-muted focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-default disabled:opacity-50 sm:h-7 sm:min-w-24 dark:bg-input/30";

  type Scope = "month" | "usual";
  let editing = $state(false);
  let at = $state("");   // the month the editor was opened on: what it saves to, whatever is on screen by then
  let scope = $state<Scope>("usual");
  let draft = $state("");
  let typed = false;   // the amount was changed by hand: switching scope keeps it rather than showing that scope's amount
  let saving = $state(false);
  let error = $state("");
  let amountBox = $state<HTMLInputElement>();
  const atName = $derived(monthShort(at || month, true));
  const atLong = $derived(monthLabel(at || month));
  const scopeOf = $derived<Scope>(canMonth ? scope : "usual");
  function amountIn(s: Scope): string {
    const v = s === "month" ? (c.month_budget ?? c.budget) : usual;
    return v == null ? "" : String(v);
  }
  const blank = $derived(draft.trim() === "");
  const zero = $derived(!blank && Number(draft) === 0);
  // Nothing to save: an empty box where there's nothing to clear.
  const nothing = $derived(blank && (scopeOf === "month" ? !ownMonth : c.budget == null));
  // What saving would do, said before anything is sent.
  const scopeNote = $derived(scopeOf === "month"
    ? `Applies to ${atLong} only. Other months keep ${money(usual)}.`
    : `Used in months without their own amount.${ownMonth ? ` ${atLong} keeps its own ${money(c.month_budget)}.` : ""}`);
  const outcome = $derived(scopeOf === "month"
    ? (blank ? (ownMonth ? `No amount takes ${atLong} back to the usual ${money(usual)}.` : `Enter an amount for ${atLong}.`)
      : zero ? `$0 budgets nothing in ${atLong}; other months keep ${money(usual)}.` : "")
    : (blank || zero) && c.budget != null ? `No amount removes the ${c.name} budget from every month, months with their own amount included.` : "");

  function start() {
    if (locked) return;
    at = month; scope = ownMonth ? "month" : "usual"; draft = amountIn(scope); typed = false; error = ""; editing = true;
  }
  function cancel() { editing = false; error = ""; }
  function pickScope(v: string) {
    scope = v as Scope; error = "";
    if (!typed) draft = amountIn(scope);
  }
  function focusAmount(e: Event) {
    e.preventDefault();
    amountBox?.focus();
    amountBox?.select();
  }
  // Another month on screen while the editor is open (and not saving): it closes without sending anything, so nothing
  // can land on a month other than the one it names.
  $effect(() => { if (editing && month !== at && !saving) cancel(); });

  async function save() {
    if (saving || locked || nothing || !editing) return;
    const [name, m, label, s, amount, kept] = [c.name, at, atName, scopeOf, draft.trim(), c.month_budget];
    if (s === "month" && amount === "") { await resetMonth(true); return; }
    error = "";
    let failed = "";
    const ok = await act(async () => {
      const body = s === "month" ? { category: name, month: m, amount } : { category: name, amount };
      const r = await apiCall<"POST /api/budget">("/api/budget", { method: "POST", body });
      if (s === "month") {
        const raised = (r.raised ?? []).map((x) => `${x.category} raised to ${fmt0(x.amount)} in ${label} only`);
        toast.success([`${name}: ${money(Number(amount))} in ${label} only`, ...raised].join(" · "));
      } else if (amount === "" || Number(amount) === 0) {
        toast.success(`${name} budget removed`);
      } else {
        const raised = (r.raised ?? []).map((x) => `${x.category} usual amount raised to ${fmt0(x.amount)}`);
        toast.success([`${name}: usual amount ${money(Number(amount))} a month`, ...raised,
          ...(kept != null ? [`${label} keeps its own ${money(kept)}`] : [])].join(" · "));
      }
    }, { busy: (on) => (saving = on), onError: (msg) => (failed = msg) });
    if (ok) { editing = false; onchanged(); return; }
    // The editor stays open with the error, so it can be tried again; if it was closed (or another month came on
    // screen) meanwhile, the error is a toast instead.
    if (editing && month === m) error = failed;
    else { editing = false; toast.error(failed); }
  }

  // The month goes back to the usual amount ("Reset to usual" under the row; "Reset this month to usual", or saving an
  // empty amount for the month, in the editor). The toast's Undo sets the month's own amount again, exactly as it was
  // (0 included), for the month and category it was taken from, whatever is on screen by then. A failed reset leaves
  // the month's amount as it is.
  let resetting = $state(false);
  async function resetMonth(fromEditor = false) {
    const m = fromEditor ? at : month;
    const [name, was, label, back] = [c.name, c.month_budget, monthShort(m, true), usual];
    const post = (amount: string) => apiCall<"POST /api/budget">("/api/budget", { method: "POST", body: { category: name, month: m, amount } });
    let failed = "";
    const done = await act(async () => {
      await post("");
      if (was != null) {
        undoable(`${name} is back to ${fmt0(back)} in ${label}`, async () => {
          await post(String(was));
          onchanged();
          return `${name}: ${money(was)} in ${label} only`;
        });
      }
    }, { busy: (on) => { resetting = on; if (fromEditor) saving = on; }, onError: fromEditor ? (msg) => (failed = msg) : undefined });
    if (fromEditor) {
      if (done) editing = false;
      else if (editing && month === m) error = failed;
      else { editing = false; toast.error(failed); }
    }
    onchanged();   // the page reloads either way, so a failed reset shows what's saved again
  }

  // Rolling over: what's left at the end of a month adds to the next, starting this month.
  async function setRollover(on: boolean) {
    await act(async () => {
      await apiCall<"POST /api/budget">("/api/budget", { method: "POST", body: { category: c.name, rollover: on } });
      toast.success(on ? `${c.name} rolls over from this month on` : `${c.name} no longer rolls over`);
    });
    onchanged();
  }
</script>

<div class={cn("py-1", sub && "pl-5")}>
  <div class="flex min-h-8 flex-wrap items-center gap-x-2.5 phone:gap-x-1.5">
    <span class="relative shrink-0">
      <CatIcon name={c.name} size={sub ? 20 : 28} />
      <!-- The account as a small badge on the emoji, as Transactions puts it on a merchant's logo; not on a phone. -->
      {#if account}<BankBadge accountId={account} name={accountName ?? ""} size={sub ? "size-3 rounded text-[7px]" : "size-4 rounded-md text-[9px]"}
        class={cn("pointer-events-auto phone:hidden", sub ? "-right-1 -bottom-1" : "lg:-right-1.5 lg:-bottom-1.5")} />{/if}
    </span>
    <a href="#transactions" onclick={open}
      class={cn("max-w-full min-w-0 truncate hover:underline", sub ? "text-muted-foreground" : "font-semibold")}>{c.name}</a>
    {#if canRoll && c.rollover_from}
      <span class="inline-flex items-center gap-1 rounded-md px-1.5 text-xs whitespace-nowrap text-primary"
        title={`What's left each month carries into the next (since ${monthShort(c.rollover_from, true)}); the row's ⋯ menu turns it off`}>
        <Repeat class="size-3" aria-hidden="true" />Rolls over
      </span>
    {/if}
    <span class="ml-auto inline-flex shrink-0 items-center gap-1.5 phone:gap-1 whitespace-nowrap tabular-nums">
      <a href="#transactions" onclick={open} class={cn("hover:underline", sub && warnOver && "font-semibold text-destructive", sub && overSoon && "text-destructive")}
        title={subNote || undefined}>{money(c.spent)}{#if subNote}<span class="sr-only">{` (${subNote})`}</span>{/if}</a>
      {#if c.budget != null || budgets}<span class="text-muted-foreground">of</span>{/if}
      <!-- The amount looks like a field, with a pencil, so it reads as something to change; it opens the editor. Its name
           says the amount and which months it is. -->
      {#if viewport.phone}
        <button type="button" disabled={locked} onclick={start} aria-haspopup="dialog" aria-expanded={editing}
          aria-label={fieldName} class={fieldCls} data-budget-field>{@render field()}</button>
      {:else}
        <Popover.Root bind:open={() => editing, (o) => (o ? start() : cancel())}>
          <Popover.Trigger disabled={locked} aria-label={fieldName} class={fieldCls} data-budget-field>{@render field()}</Popover.Trigger>
          <Popover.Content role="dialog" class="w-80" align="end" onOpenAutoFocus={focusAmount} aria-labelledby={`${uid}-title`}>
            <h2 id={`${uid}-title`} class="mb-3 text-[15px] font-semibold">{title}</h2>
            {@render editor()}
          </Popover.Content>
        </Popover.Root>
      {/if}
    </span>
  </div>
  {#if c.budget != null && !sub}
    <div class="flex items-center gap-3 sm:pl-[38px]">
      <div class="relative h-2 min-w-28 flex-1 rounded-full bg-muted" role="img"
        aria-label={income ? `${Math.round(pct * 100)}% of expected income received${coming ? `, ${Math.round(expShare * 100)}% more scheduled` : ""}`
          : `${Math.round(pct * 100)}% of budget used${coming ? `, ${Math.round(expShare * 100)}% more coming` : ""}`}>
        <!-- What's still coming: drawn from the start, under what's spent, so the lighter part shows after it. -->
        {#if coming && !over}
          <div class={cn("absolute inset-y-0 left-0 rounded-full opacity-45", overSoon && "bg-destructive")} style:background={overSoon ? undefined : color}
            style:width={barWidth(pct + expShare)} data-expected></div>
        {/if}
        <div class={cn("relative h-full rounded-full", warnOver && "bg-destructive")} style:background={warnOver ? undefined : color}
          style:width={barWidth(pct)}></div>
        {#if showPace}
          <div class="absolute -top-[3px] -bottom-[3px] w-0.5 rounded-sm bg-muted-foreground" style:left={`${(pace * 100).toFixed(1)}%`}
            title="Where you'd be at an even pace today"></div>
        {/if}
      </div>
      <!-- At least wide enough for "▲ $1,234 over with what’s coming", so the bars end at the same x whatever it says (a longer
           text, with five-figure amounts, takes the room it needs from its bar). -->
      <span class="shrink-0 text-right text-xs whitespace-nowrap tabular-nums sm:min-w-52">
        {#if income}
          <!-- What's still to come of what's expected, and how much of it is scheduled (paychecks in the forecast). -->
          {#if over}<span class="font-semibold text-good">▲ {money(c.spent - avail!)} over</span>
          {:else}<span class="text-muted-foreground">{money(avail! - c.spent)} to come{coming ? ` · ${money(expected)} scheduled` : ""}</span>{/if}
        {:else if over}<span class="font-semibold text-destructive">▲ {money(c.spent - avail!)} over</span>
        {:else if overSoon}<span class="text-destructive">▲ {money(c.spent + expected - avail!)} over with what’s coming</span>
        {:else if coming}<span class="text-muted-foreground">{money(Math.max(0, avail! - c.spent - expected))} left · {money(expected)} coming</span>
        {:else if showPace && c.spent > avail! * pace * 1.1}<span class="text-muted-foreground">{money(c.left)} left · ahead of pace</span>
        {:else if Math.abs(c.spent) > 0.005}<span class="text-muted-foreground">{money(c.left)} left</span>{/if}
      </span>
      {#if canRoll}
        <Button variant="ghost" size="icon" class={cn("-my-2 -mr-1.5 -ml-1.5 size-8 shrink-0 text-muted-foreground hover:text-foreground focus-visible:opacity-100",
          menu ? "opacity-100" : "hoverable:opacity-0 hoverable:group-hover/family:opacity-100 hoverable:group-focus-within/family:opacity-100")}
          aria-label={`Actions for ${c.name}`} aria-expanded={menu} disabled={locked} onclick={() => (menu = !menu)}><Ellipsis /></Button>
      {/if}
    </div>
  {/if}
  {#if menu && canRoll}
    <div class="flex flex-wrap items-center gap-x-3 gap-y-2 px-1 pt-1 pb-1 sm:pl-[38px]">
      <button type="button" aria-pressed={!!c.rollover_from} disabled={locked} onclick={() => setRollover(!c.rollover_from)}
        title={c.rollover_from ? `What's left each month carries into the next (since ${monthShort(c.rollover_from, true)}). Click to stop.`
          : "Carry what's left at the end of each month into the next"}
        class={cn("inline-flex cursor-pointer items-center gap-1 rounded-md border disabled:cursor-default disabled:opacity-50 px-2.5 py-1.5 text-sm whitespace-nowrap hover:bg-muted focus-visible:bg-muted focus-visible:outline-none",
          c.rollover_from ? "border-primary/40 text-primary" : "border-transparent text-muted-foreground")}>
        <Repeat class="size-3.5" aria-hidden="true" />{c.rollover_from ? "Rolls over" : "Roll over"}
      </button>
      <Button variant="link" size="sm" class="h-auto justify-start p-0" onclick={() => (menu = false)}>Done</Button>
    </div>
  {/if}
  {#if c.budget != null && carried > 0.005}
    <p class={cn("mt-0.5 text-xs text-muted-foreground tabular-nums", sub ? "sm:pl-[30px]" : "sm:pl-[38px]")}>{fmt0(c.budget)} + {money(carried)} rolled over from earlier months</p>
  {/if}
  {#if canMonth && ownMonth}
    <p class={cn("mt-0.5 text-xs text-muted-foreground tabular-nums", sub ? "sm:pl-[30px]" : "sm:pl-[38px]")} data-month-note>
      {monthName} only · {money(usual)} other months
      <!-- Always shown (not on hover), a real button: reachable by keyboard, and a big enough target on touch. -->
      <button type="button" onclick={() => resetMonth()} disabled={resetting || locked} data-month-reset
        aria-label={`Reset to usual: ${c.name} back to ${fmt0(usual)} in ${monthName}`}
        class="-my-2 ml-1 inline-flex cursor-pointer items-center rounded-md px-1.5 py-2.5 font-medium text-primary underline underline-offset-2 hover:bg-muted focus-visible:bg-muted focus-visible:outline-none disabled:cursor-default disabled:opacity-50 sm:-my-1 sm:py-1.5">
        Reset to usual</button>
    </p>
  {/if}
</div>

{#if viewport.phone}
  <Sheet.Root bind:open={() => editing, (o) => { if (!o) cancel(); }}>
    <Sheet.Content onOpenAutoFocus={focusAmount} data-budget-sheet>
      <Sheet.Header class="pb-0">
        <Sheet.Title>{title}</Sheet.Title>
      </Sheet.Header>
      <div class="px-4 pb-4">{@render editor()}</div>
    </Sheet.Content>
  </Sheet.Root>
{/if}

{#snippet field()}
  <span class={cn("min-w-0 truncate", c.budget == null ? "text-primary" : ownMonth && canMonth && "text-primary")}>
    {c.budget != null ? money(c.budget) : sub ? "—" : income ? "Expected" : "Budget"}</span>
  <Pencil class="size-3 shrink-0 text-muted-foreground" aria-hidden="true" />
{/snippet}

<!-- The editor: the amount, which months it's for and what that means, then Save and Cancel. -->
{#snippet editor()}
  <form class="flex flex-col gap-3" data-budget-editor onsubmit={(e) => { e.preventDefault(); save(); }}>
    <div class="flex flex-col gap-1.5">
      <label for={`${uid}-amount`} class="text-xs text-muted-foreground">Amount</label>
      <span class="relative">
        <span aria-hidden="true" class="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-base text-muted-foreground md:text-sm">$</span>
        <!-- 16px text on a phone, so the browser doesn't zoom in on it. Read-only (not disabled) while saving, so it keeps focus. -->
        <input bind:this={amountBox} id={`${uid}-amount`} type="number" min="0" step="10" value={draft} {@attach commas} readonly={saving}
          placeholder={income ? "Expected" : "No budget"} aria-describedby={`${uid}-scope-note ${uid}-outcome`}
          oninput={(e) => { draft = e.currentTarget.value; typed = true; error = ""; }}
          class="h-11 w-full rounded-lg border border-input bg-transparent pr-3 pl-6 text-base tabular-nums outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 md:h-9 md:text-sm dark:bg-input/30" />
      </span>
    </div>
    {#if canMonth}
      <div class="flex flex-col gap-1.5">
        <span class="text-xs text-muted-foreground" aria-hidden="true">Applies to</span>
        <Segmented label="Applies to" value={scope} class="w-full" onchange={pickScope}
          options={[{ value: "month", label: "This month only", disabled: saving }, { value: "usual", label: "Usual monthly amount", disabled: saving }]} />
      </div>
    {:else}
      <p class="text-xs text-muted-foreground">Applies to: <span class="font-medium text-foreground">usual monthly amount</span></p>
    {/if}
    <p id={`${uid}-scope-note`} class="text-xs text-muted-foreground" data-scope-note>{scopeNote}</p>
    <p id={`${uid}-outcome`} class="text-xs text-foreground empty:hidden" data-outcome>{outcome}</p>
    {#if error}<p role="alert" class="text-sm text-destructive">{error}</p>{/if}
    {#if locked}<p class="text-xs text-muted-foreground" data-locked>Saving is off until the page’s figures are up to date.</p>{/if}
    <div class="flex flex-wrap items-center justify-end gap-2">
      {#if canMonth && ownMonth}
        <Button variant="link" size="sm" class="mr-auto h-auto px-0 py-2" disabled={saving || locked} onclick={() => resetMonth(true)}
          aria-label={`Reset this month to usual: ${c.name} back to ${fmt0(usual)} in ${atName}`}>Reset this month to usual</Button>
      {/if}
      <Button variant="outline" size="sm" onclick={cancel}>Cancel</Button>
      <Button type="submit" size="sm" disabled={saving || locked || nothing}>{saving ? "Saving…" : "Save"}</Button>
    </div>
  </form>
{/snippet}
