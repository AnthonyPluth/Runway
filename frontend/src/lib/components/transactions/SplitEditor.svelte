<script lang="ts">
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

  // Spread one transaction across categories: each part gets its own category and amount, and they must add up.
  // Amounts are typed as plain numbers; the transaction's own sign (a charge or a deposit) is kept.
  let { t, onclose, onsaved }: { t: Tx; onclose: () => void; onsaved: () => void } = $props();

  type Part = { key: number; category: string; amount: string; note: string };
  let n = 0;
  const part = (category = "", amount = "", note = ""): Part => ({ key: n++, category, amount, note });
  // svelte-ignore state_referenced_locally
  const sign = t.amount < 0 ? -1 : 1, total = Math.abs(t.amount);
  // svelte-ignore state_referenced_locally
  const existing = (t.splits ?? []).map((s) => part(s.category, Math.abs(s.amount).toFixed(2), s.note ?? ""));
  // svelte-ignore state_referenced_locally
  let parts = $state(existing.length ? existing : [part(t.category ?? "", total.toFixed(2)), part()]);

  const num = (s: string | number) => parseFloat(String(s));
  const rest = $derived(Math.round((total - parts.reduce((s, p) => s + (isFinite(num(p.amount)) ? num(p.amount) : 0), 0)) * 100) / 100);

  function add() { parts.push(part("", rest > 0 ? rest.toFixed(2) : "")); }

  async function send(body: unknown, msg: string) {
    try {
      await api(`/api/transactions/${encodeURIComponent(t.id)}/split`, { method: "POST", body });
      toast.success(msg);
      refreshState();
      onsaved();
    } catch (err) { toast.error((err as Error).message); }
  }
  function save() {
    if (parts.some((p) => !p.category)) return toast.error("Give every part a category");
    if (parts.some((p) => !isFinite(num(p.amount)) || num(p.amount) <= 0)) return toast.error("Give every part an amount");
    send({ splits: parts.map((p) => ({ category: p.category, amount: sign * num(p.amount), note: p.note })) }, "Split saved");
  }
  function focusFirst(el: HTMLElement) { el.querySelector("select")?.focus(); }
</script>

<div class="flex flex-col gap-3 rounded-lg bg-muted/40 p-4 text-sm" data-editor use:focusFirst>
  <div class="flex items-baseline gap-2"><b>Split {fmt(total)}</b> <span class="truncate text-xs text-muted-foreground">{t.payee || t.description}</span></div>
  <div class="flex flex-col gap-2">
    {#each parts as p, i (p.key)}
      <div class="flex flex-wrap items-center gap-2">
        <CategorySelect short bind:value={p.category} label={`Category of part ${i + 1}`} class="w-full sm:w-56" />
        <Input type="number" step="0.01" min="0" inputmode="decimal" aria-label={`Amount of part ${i + 1}`} bind:value={p.amount}
          class="w-28 text-right tabular-nums" />
        <Input placeholder="Note (optional)" aria-label={`Note for part ${i + 1}`} bind:value={p.note} class="min-w-32 flex-1" />
        <Button variant="ghost" size="icon" class="size-10 sm:size-8" title="Remove this part" aria-label="Remove this part"
          onclick={() => parts.splice(i, 1)}><X /></Button>
      </div>
    {/each}
  </div>
  <div class="flex flex-wrap items-center gap-3">
    <Button variant="link" size="sm" class="h-auto p-0" onclick={add}>+ Add a part</Button>
    <span class={cn("tabular-nums text-muted-foreground", rest < 0 && "text-destructive")} aria-live="polite">
      {rest ? `${fmt(Math.abs(rest))} ${rest > 0 ? "left to assign" : "over"}` : "adds up"}</span>
    <span class="ml-auto flex flex-wrap items-center gap-2">
      {#if existing.length}<Button variant="link" size="sm" onclick={() => send({ splits: [] }, "Split removed")}>Remove split</Button>{/if}
      <Button variant="outline" size="sm" onclick={onclose}>Cancel</Button>
      <Button size="sm" onclick={save}>Save split</Button>
    </span>
  </div>
</div>
