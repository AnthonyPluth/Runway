<script lang="ts">
  import { api } from "$lib/api";
  import type { RecurringItem } from "$lib/components/recurring/types";
  import { NativeSelect } from "$lib/components/ui/native-select";
  import { toast } from "svelte-sonner";
  import { askAlsoMatch } from "./remember.svelte";
  import type { Tx } from "./types";

  // Link a transaction to a recurring item, start a new one from it, or mark it as not recurring. Leaving the picker
  // without choosing closes it.
  let { t, items, onclose, onchanged }: { t: Tx; items: RecurringItem[]; onclose: () => void; onchanged: () => void } = $props();
  const same = $derived(items.filter((r) => r.account_id === t.account_id));
  const other = $derived(items.filter((r) => r.account_id !== t.account_id));
  let value = $state("");
  let busy = false;

  async function pick() {
    if (!value) return;
    busy = true;
    const body = value.startsWith("new:") ? { new: value.slice(4) } : value === "none" ? { recurring_id: null } : { recurring_id: Number(value) };
    try {
      const r = await api<{ suggest_text?: string }>(`/api/transactions/${encodeURIComponent(t.id)}/recurring`, { method: "POST", body });
      // None of the item's texts is on this one: offer its text, so the next one links by itself.
      const item = "recurring_id" in body && r?.suggest_text ? items.find((i) => i.id === body.recurring_id) : undefined;
      if (item && r.suggest_text) askAlsoMatch(item.id, item.name, r.suggest_text, onchanged);
      else toast.success("new" in body ? "Recurring item created; edit it in Recurring" : body.recurring_id ? "Linked" : "Marked as not recurring");
    } catch (err) { toast.error((err as Error).message); }
    onclose();
    onchanged();
  }
  function focus(el: HTMLElement) { el.focus(); }
</script>

{#snippet opt(r: RecurringItem)}<option value={String(r.id)}>{r.name} · {r.frequency}</option>{/snippet}

<span class="contents" data-editor>
  <NativeSelect bind:value aria-label="Recurring item for this transaction" class="h-7 text-xs" onchange={pick}
    onblur={() => { if (!busy) onclose(); }} {@attach focus}>
    <option value="">Recurring…</option>
    {#if same.length}<optgroup label="Link to">{#each same as r (r.id)}{@render opt(r)}{/each}</optgroup>{/if}
    {#if other.length}<optgroup label="Other accounts">{#each other as r (r.id)}{@render opt(r)}{/each}</optgroup>{/if}
    <optgroup label="New recurring item from this">
      <option value="new:monthly">Monthly</option><option value="new:biweekly">Every 2 weeks</option>
      <option value="new:weekly">Weekly</option><option value="new:yearly">Yearly</option>
    </optgroup>
    {#if (t.recurring_id ?? 0) > 0}<option value="none">Not recurring</option>{/if}
  </NativeSelect>
</span>
