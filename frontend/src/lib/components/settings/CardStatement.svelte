<script lang="ts">
  import { commas } from "$lib/commas";
  import { api } from "$lib/api";
  import { reload } from "$lib/app.svelte";
  import { Button } from "$lib/components/ui/button";
  import { fmt, fmtDate, isoDay, nb } from "$lib/format";
  import { undoable } from "$lib/undo";
  import { toast } from "svelte-sonner";
  import type { SettingsAccount } from "./types";
  import { fieldCls, inputCls, rowCls, warnText } from "./ui";

  // A card's statement, in its row in Settings → Accounts: Plaid's, read only, when the bank sends it; otherwise the
  // latest one you entered and a form to enter the next (closing date, balance, due date, minimum), with the earlier ones.
  // The forecast uses it to put the card's payment on its due date (runway/statements.py).
  let { a, note = "" }: { a: SettingsAccount; note?: string } = $props();

  const st = $derived(a.statement ?? null);
  const entered = $derived(a.statements ?? []);
  const earlier = $derived(st?.source === "manual" ? entered.slice(1) : entered);
  // With one entered, the form waits behind "Enter the next statement"; with none, it's open.
  let adding = $state(false);
  const showForm = $derived(st?.source !== "plaid" && (adding || !st));
  const today = isoDay();
  const SHOWN = 6;

  let close = $state(""), balance = $state(""), due = $state(""), minimum = $state("");
  let saving = $state(false);

  const line = (s: { balance?: number | null; closed: string; due?: string | null; minimum?: number | null }) =>
    `${fmt(s.balance)} · closed ${fmtDate(s.closed)}${s.due ? ` · due ${fmtDate(s.due)}` : ""}${s.minimum != null ? ` · min ${fmt(s.minimum)}` : ""}`;

  async function save(e: SubmitEvent) {
    e.preventDefault();
    saving = true;
    try {
      await api(`/api/accounts/${encodeURIComponent(a.id)}/statements`, { method: "POST",
        body: { statement_date: close, balance, due_date: due, minimum_payment: minimum } });
      toast.success("Statement saved");
      reload();
    } catch (err) { toast.error((err as Error).message); }
    finally { saving = false; }
  }
  // Deleting one offers Undo, which enters it again just as it was.
  async function remove(date: string) {
    const was = entered.find((s) => s.statement_date === date);
    const id = a.id;
    try {
      await api(`/api/accounts/${encodeURIComponent(id)}/statements/${encodeURIComponent(date)}/remove`, { method: "POST" });
      reload();
    } catch (err) { toast.error((err as Error).message); return; }
    if (!was) { toast.success("Statement deleted"); return; }
    undoable("Statement deleted", async () => {
      await api(`/api/accounts/${encodeURIComponent(id)}/statements`, { method: "POST", body: { statement_date: was.statement_date,
        balance: was.balance, due_date: was.due_date, minimum_payment: was.minimum_payment ?? "" } });
      reload();
    });
  }
</script>

<h4 class="text-sm font-medium">Statement</h4>
{#if st?.source === "plaid"}
  <p class="text-sm tabular-nums">{line(st)}</p>
  <p class="text-xs text-muted-foreground">From {st.institution || "the bank"} via Plaid</p>
{:else}
  {#if st}
    <p class="flex flex-wrap items-center gap-x-2 text-sm tabular-nums">{line(st)}
      <Button variant="link" size="sm" class="h-auto p-0 text-xs" aria-label={`Delete the statement that closed ${fmtDate(st.closed)}`}
        onclick={() => remove(st.closed)}>Delete</Button></p>
    {#if st.stale}
      <p class={`text-xs ${warnText}`}>Entered by you · the next one closed around {nb(fmtDate(st.next_close ?? st.closed))}: enter it so the card’s payment stays in the forecast</p>
    {:else}
      <p class="text-xs text-muted-foreground">Entered by you</p>
    {/if}
  {:else}
    <p class="text-sm text-muted-foreground">{note ? `${note} ` : ""}Enter the latest statement from the card’s website or app.</p>
  {/if}
  {#if showForm}
    <form class={rowCls} aria-label="Enter a statement" onsubmit={save}>
      <label class={fieldCls}>Closing date
        <input class={`${inputCls} w-40`} type="date" max={today} required bind:value={close} /></label>
      <label class={fieldCls}>Statement balance
        <input class={`${inputCls} w-32`} type="number" step="0.01" min="0" inputmode="decimal" required bind:value={balance} {@attach commas} /></label>
      <label class={fieldCls}>Due date
        <input class={`${inputCls} w-40`} type="date" min={close || undefined} required bind:value={due} /></label>
      <label class={fieldCls}>Minimum (optional)
        <input class={`${inputCls} w-28`} type="number" step="0.01" min="0" inputmode="decimal" bind:value={minimum} {@attach commas} /></label>
      <span class="flex gap-2">
        <Button type="submit" size="sm" disabled={saving}>{saving ? "Saving…" : "Save statement"}</Button>
        {#if st}<Button type="button" variant="ghost" size="sm" onclick={() => (adding = false)}>Cancel</Button>{/if}
      </span>
    </form>
  {:else}
    <span><Button variant="outline" size="sm" data-enter-next onclick={() => (adding = true)}>Enter the next statement</Button></span>
  {/if}
  {#if earlier.length}
    <div class="text-sm">
      <h5 class="text-xs font-medium text-muted-foreground">Earlier</h5>
      <ul class="mt-1 flex flex-col gap-1" aria-label="Earlier statements">
        {#each earlier.slice(0, SHOWN) as s (s.statement_date)}
          <li class="flex flex-wrap items-center gap-x-2 tabular-nums text-muted-foreground">
            {line({ balance: s.balance, closed: s.statement_date, due: s.due_date, minimum: s.minimum_payment })}
            <Button variant="link" size="sm" class="h-auto p-0 text-xs" aria-label={`Delete the statement that closed ${fmtDate(s.statement_date)}`}
              onclick={() => remove(s.statement_date)}>Delete</Button>
          </li>
        {/each}
        {#if earlier.length > SHOWN}<li class="text-xs text-muted-foreground">and {earlier.length - SHOWN} older</li>{/if}
      </ul>
    </div>
  {/if}
{/if}
