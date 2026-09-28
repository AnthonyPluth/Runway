<script lang="ts">
  import { api } from "$lib/api";
  import * as Alert from "$lib/components/ui/alert";
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
    toast("Use ↻ on the payment to link it to this recurring item");
  }
  async function dismiss() {
    try { await api("/api/recurring/dismiss", { method: "POST", body: { key: m.key } }); gone = true; ondismiss?.(m.key); toast.success("Dismissed"); }
    catch (err) { toast.error((err as Error).message); }
  }
</script>

{#if !gone}
  <Alert.Root class="mb-3">
    <TriangleAlert />
    <Alert.Description><p>
      <b>{m.name}</b>: {fmt(Math.abs(m.amount))} {m.amount > 0 ? "expected in" : "expected"} {fmtDate(m.date)} hasn't shown up in {m.account_name || "the account"}.
      <a class="font-medium text-foreground underline underline-offset-4" href="#transactions" onclick={find}>Find it</a> ·
      <button type="button" class="cursor-pointer font-medium text-foreground underline underline-offset-4" onclick={dismiss}>Dismiss</button>
    </p></Alert.Description>
  </Alert.Root>
{/if}
