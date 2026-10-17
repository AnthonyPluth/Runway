<script lang="ts">
  import { app } from "$lib/app.svelte";
  import { catLook, categories } from "$lib/categories.svelte";
  import CategorySelect from "$lib/components/CategorySelect.svelte";
  import Logo from "$lib/components/Logo.svelte";
  import { Button } from "$lib/components/ui/button";
  import { fmtDate, fmtSigned } from "$lib/format";
  import { cn } from "$lib/utils";
  import { viewport } from "$lib/phone.svelte";
  import ChevronDown from "@lucide/svelte/icons/chevron-down";
  import { txLogo } from "./logo";
  import { alternatives } from "./reviewCard";
  import type { Tx } from "./types";

  // The top of All when something is waiting for review: one transaction at a time, "1 of N". Its category is the
  // suggestion, up to two others used on the rows at hand, and "Other…" (the category picker). Accept keeps the chosen
  // category and moves to the next; Skip moves on without changing anything (it comes round again). `onsubmit` is the
  // page's: the existing accept (the suggestion kept) or category save (another picked); true when it was saved,
  // when the transaction leaves `items`.
  // On a phone it starts as one line, "N to review" with Review next and See all; Review next opens the card as it is
  // (`open`, bound by the page so it survives reloads).
  let { items, total, rows, onsubmit, onmore, open = $bindable(false) }: {
    open?: boolean;
    items: Tx[]; total: number; rows: Tx[]; onsubmit: (t: Tx, category: string) => Promise<boolean>;
    /** Adds the next batch of the queue to `items` (there are more than are loaded). */
    onmore?: () => Promise<void>;
  } = $props();

  let at = $state(0);
  let picks = $state<Record<string, string>>({});
  let busy = $state(false);
  let other = $state("");

  const t = $derived(items.length ? items[at % items.length] : undefined);
  const chosen = $derived(t ? (picks[t.id] ?? t.category ?? "") : "");
  const options = $derived(t ? [...new Set([t.category ?? "", ...alternatives(t, [...items, ...rows], categories.list), ...(chosen ? [chosen] : [])].filter(Boolean))] : []);
  const shown = $derived(t ? txLogo(t, app.state?.brands) : null);
  const name = $derived(t ? (t.payee || t.description || "") : "");
  const initial = $derived(shown?.bank?.initial || ((name.replace(/^[^A-Za-z0-9]+/, "")[0]) || "?").toUpperCase());

  async function skip() {
    // At the last one loaded, with more waiting: load them before moving on, so it doesn't wrap to the first.
    if (onmore && items.length < total && at % items.length === items.length - 1) { busy = true; try { await onmore(); } finally { busy = false; } }
    if (items.length > 1) at = ((at % items.length) + 1) % items.length;
  }
  async function accept() {
    if (!t || !chosen || busy) return;
    busy = true;
    try { await onsubmit(t, chosen); } finally { busy = false; }
  }
</script>

{#if t && viewport.phone}
  <div class="mb-3 flex min-h-11 items-center justify-between gap-3 rounded-xl border bg-card px-4" data-review-summary>
    <p class="text-sm font-medium tabular-nums">{Math.max(total, items.length)} to review</p>
    <div class="flex items-center gap-3 text-sm font-medium">
      <button type="button" class="inline-flex min-h-11 cursor-pointer items-center gap-1 text-primary" aria-expanded={open} aria-controls={open ? "review-card" : undefined}
        onclick={() => (open = !open)}>Review next<ChevronDown class={cn("size-4 transition-transform", open && "rotate-180")} aria-hidden="true" /></button>
      <a href="#review" class="inline-flex min-h-11 items-center text-primary">See all</a>
    </div>
  </div>
{/if}
{#if t && (open || !viewport.phone)}
  <section id="review-card" aria-label="To review" data-review-card class="mb-4 rounded-2xl border bg-card p-4">
    <div class="mb-3 flex items-center justify-between text-[13px]">
      <h2 class="font-semibold tracking-wide text-muted-foreground uppercase">To review · {(at % items.length) + 1} of {Math.max(total, items.length)}</h2>
      <a href="#review" class="font-medium text-primary">See all</a>
    </div>
    <div class="flex items-center gap-3">
      {#if shown?.src}
        <Logo src={shown.src} size={40} />
      {:else}
        <span class="flex size-10 shrink-0 items-center justify-center rounded-lg bg-muted text-sm font-semibold text-muted-foreground" aria-hidden="true">{initial}</span>
      {/if}
      <div class="min-w-0 flex-1">
        <p class="truncate font-medium" title={name}>{name}</p>
        <p class="truncate text-xs text-muted-foreground">{fmtDate(t.posted)} · {t.account_name}</p>
      </div>
      <p class={cn("whitespace-nowrap tabular-nums", t.amount > 0 ? "font-semibold text-good" : "font-medium")}>{fmtSigned(t.amount)}</p>
    </div>
    <div class="mt-3 flex flex-wrap items-center gap-2" role="group" aria-label="Category">
      {#each options as c (c)}
        <button type="button" aria-pressed={chosen === c} disabled={busy} onclick={() => (picks[t.id] = c)}
          class={cn("inline-flex h-9 cursor-pointer items-center gap-1.5 rounded-full border px-3 text-sm transition-colors",
            chosen === c ? "border-primary bg-primary/15 text-primary" : "border-transparent bg-muted hover:border-muted-foreground/40")}>
          <span aria-hidden="true">{catLook(c).icon}</span>{c}</button>
      {/each}
      <CategorySelect bind:value={other} blank={false} label={`Other category for ${name}`} disabled={busy}
        class="h-9 cursor-pointer items-center rounded-full border border-dashed px-3 text-sm text-muted-foreground"
        onchange={(v) => { if (v) picks[t.id] = v; other = ""; }}>Other…</CategorySelect>
    </div>
    <div class="mt-4 grid grid-cols-2 gap-3">
      <Button variant="outline" size="lg" class="h-12 text-base" disabled={busy || items.length < 2} onclick={skip}>Skip</Button>
      <Button size="lg" class="h-12 text-base" disabled={busy || !chosen} onclick={accept}>Accept</Button>
    </div>
  </section>
{/if}
