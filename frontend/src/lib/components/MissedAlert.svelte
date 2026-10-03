<script lang="ts">
  import { api } from "$lib/api";
  import { showTransactions } from "$lib/filters.svelte";
  import { fmt, fmtDate } from "$lib/format";
  import type { Missed } from "$lib/types";
  import { toast } from "svelte-sonner";
  import TriangleAlert from "@lucide/svelte/icons/triangle-alert";

  // A recurring payment that hasn't shown up: "Find it" opens Transactions around that date, "Dismiss" forgets it.
  let { m, ondismiss }: { m: Missed; ondismiss?: (key: string) => void } = $props();
  let gone = $state(false);

  function find(e: Event) {
    e.preventDefault();
    showTransactions({ account: m.account_id ?? "", month: m.date.slice(0, 7) });
    toast("Use the repeat icon on the payment to link it to this recurring item");
  }
  async function dismiss() {
    try { await api("/api/recurring/dismiss", { method: "POST", body: { key: m.key } }); gone = true; ondismiss?.(m.key); toast.success("Dismissed"); }
    catch (err) { toast.error((err as Error).message); }
  }
</script>

<!-- A row of the Overview's Needs attention list. -->
{#if !gone}
  <div class="cell">
    <span class="flex size-8 shrink-0 items-center justify-center rounded-lg bg-warning text-black" aria-hidden="true"><TriangleAlert class="size-4" /></span>
    <p class="min-w-0 flex-1 text-sm">
      <b class="font-medium">{m.name}</b>: {fmt(Math.abs(m.amount))} {m.amount > 0 ? "expected in" : "expected"} {fmtDate(m.date)} hasn't shown up in {m.account_name || "the account"}.
    </p>
    <span class="flex shrink-0 gap-3 text-sm">
      <a class="font-medium text-primary" href="#transactions" onclick={find}>Find it</a>
      <button type="button" class="cursor-pointer text-muted-foreground hover:text-foreground" onclick={dismiss}>Dismiss</button>
    </span>
  </div>
{/if}
