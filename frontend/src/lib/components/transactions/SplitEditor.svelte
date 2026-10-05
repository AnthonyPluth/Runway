<script lang="ts">
  import { commas } from "$lib/commas";
  import { api } from "$lib/api";
  import { refreshState } from "$lib/app.svelte";
  import CategorySelect from "$lib/components/CategorySelect.svelte";
  import { Button } from "$lib/components/ui/button";
  import { Input } from "$lib/components/ui/input";
  import { fmt } from "$lib/format";
  import { cn } from "$lib/utils";
  import { toast } from "svelte-sonner";
  import X from "@lucide/svelte/icons/x";
  import type { Tx } from "./types";
  import { errMsg } from "$lib/act";

  // Spread one transaction across categories: each part gets its own category and amount, and they must add up.
  // Amounts are typed as plain numbers; the transaction's own sign (a charge or a deposit) is kept. Save waits until
  // every part has a category and an amount and they add up (to the cent); what's missing is said beside it. Enter
  // saves, Escape cancels, and closing puts focus back on what opened it (`returnFocus`).
  let { t, returnFocus = null, onclose, onsaved }: { t: Tx; returnFocus?: HTMLElement | null; onclose: () => void; onsaved: () => void } = $props();

  type Part = { key: number; category: string; amount: string; note: string };
  let n = 0;
  const part = (category = "", amount = "", note = ""): Part => ({ key: n++, category, amount, note });
  // svelte-ignore state_referenced_locally
  const sign = t.amount < 0 ? -1 : 1, total = Math.abs(t.amount);
  // svelte-ignore state_referenced_locally
  const existing = (t.splits ?? []).map((s) => part(s.category ?? "", Math.abs(s.amount).toFixed(2), s.note ?? ""));
  // svelte-ignore state_referenced_locally
  let parts = $state(existing.length ? existing : [part(t.category ?? "", total.toFixed(2)), part()]);

  const num = (s: string | number) => parseFloat(String(s));
  const cents = (s: string) => (isFinite(num(s)) ? Math.round(num(s) * 100) : 0);
  // What's left to assign, in cents (negative: the parts are over).
  const left = $derived(Math.round(total * 100) - parts.reduce((s, p) => s + cents(p.amount), 0));
  const rest = $derived(left / 100);

  // Why it can't be saved yet ("" once it can).
  const problem = $derived(
    parts.length < 2 ? "Add a second part"
      : parts.some((p) => p.amount !== "" && isFinite(num(p.amount)) && num(p.amount) < 0) ? "No negative amounts: the sign is the transaction’s"
      : parts.some((p) => !p.category) ? "Give every part a category"
      : parts.some((p) => !isFinite(num(p.amount)) || num(p.amount) <= 0) ? "Give every part an amount" : "");
  const ready = $derived(!problem && left === 0);
  let error = $state("");
  let busy = $state(false);

  function add() { parts.push(part("", left > 0 ? rest.toFixed(2) : "")); }
  /** Put what's left (or take what's over) on this part. */
  function assign(p: Part) { p.amount = Math.max(0, (cents(p.amount) + left) / 100).toFixed(2); }
  /** The same amount on every part, the odd cents on the first ones. */
  function evenly() {
    const all = Math.round(total * 100), each = Math.floor(all / parts.length);
    parts.forEach((p, i) => { p.amount = ((each + (i < all - each * parts.length ? 1 : 0)) / 100).toFixed(2); });
  }

  function close() { onclose(); returnFocus?.focus(); }
  async function send(body: unknown, msg: string) {
    busy = true; error = "";
    try {
      await api(`/api/transactions/${encodeURIComponent(t.id)}/split`, { method: "POST", body });
      toast.success(msg);
      refreshState();
      onsaved();
      returnFocus?.focus();
    } catch (err) { error = errMsg(err); }
    busy = false;
  }
  function save(e: SubmitEvent) {
    e.preventDefault();
    if (!ready || busy) return;
    send({ splits: parts.map((p) => ({ category: p.category, amount: sign * num(p.amount), note: p.note })) }, "Split saved");
  }
  // Escape cancels the split (and only that: a sheet around it stays open).
  function escape(el: HTMLElement) {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); } };
    el.addEventListener("keydown", onKey);
    return { destroy: () => el.removeEventListener("keydown", onKey) };
  }
  function focusFirst(el: HTMLElement) { el.querySelector("select")?.focus(); }
</script>

<form class="flex flex-col gap-3 rounded-lg bg-muted/40 p-4 text-sm" data-editor use:focusFirst use:escape onsubmit={save}
  aria-label={`Split ${t.payee || t.description || ""}`}>
  <div class="flex items-baseline gap-2"><b>Split {fmt(total)}</b> <span class="truncate text-xs text-muted-foreground">{t.payee || t.description}</span></div>
  <div class="flex flex-col gap-2">
    {#each parts as p, i (p.key)}
      <div class="flex flex-wrap items-center gap-2">
        <CategorySelect short bind:value={p.category} label={`Category of part ${i + 1}`} class="w-full sm:w-56" />
        <Input type="number" step="0.01" min="0" inputmode="decimal" aria-label={`Amount of part ${i + 1}`} bind:value={p.amount} {@attach commas}
          class="w-28 text-right tabular-nums" aria-invalid={(p.amount !== "" && num(p.amount) < 0) || undefined} />
        <Input placeholder="Note (optional)" aria-label={`Note for part ${i + 1}`} bind:value={p.note} class="min-w-32 flex-1" />
        {#if left !== 0 && cents(p.amount) + left >= 0}
          <Button variant="link" size="sm" class="h-auto px-1 text-xs" aria-label={`${left > 0 ? "Assign" : "Take off"} ${fmt(Math.abs(rest))} ${left > 0 ? "to" : "from"} part ${i + 1}`}
            title={left > 0 ? "Assign to this part" : "Take it off this part"} onclick={() => assign(p)}>{left > 0 ? "+" : "−"}{fmt(Math.abs(rest))}</Button>
        {/if}
        <Button variant="ghost" size="icon" class="size-10 sm:size-8" title="Remove this part" aria-label="Remove this part"
          onclick={() => parts.splice(i, 1)}><X /></Button>
      </div>
    {/each}
  </div>
  <div class="flex flex-wrap items-center gap-x-3 gap-y-2">
    <Button variant="link" size="sm" class="h-auto p-0" onclick={add}>+ Add a part</Button>
    <Button variant="link" size="sm" class="h-auto p-0" onclick={evenly}>Split evenly</Button>
    <span class={cn("rounded-full px-2 py-0.5 text-xs tabular-nums", left === 0 ? "bg-good/15 text-good" : left > 0 ? "bg-muted text-muted-foreground" : "bg-destructive/15 text-destructive")}
      aria-live="polite">{left === 0 ? "adds up" : `${fmt(Math.abs(rest))} ${left > 0 ? "left" : "over"}`}</span>
    <span class="ml-auto flex flex-wrap items-center gap-2">
      {#if existing.length}<Button variant="link" size="sm" disabled={busy} onclick={() => send({ splits: [] }, "Split removed")}>Remove split</Button>{/if}
      <Button variant="outline" size="sm" onclick={close}>Cancel</Button>
      <Button type="submit" size="sm" disabled={!ready || busy}>{busy ? "Saving…" : "Save split"}</Button>
    </span>
  </div>
  {#if error || problem}<p class={cn("text-xs", error ? "text-destructive" : "text-muted-foreground")} role={error ? "alert" : undefined}>{error || problem}</p>{/if}
</form>
