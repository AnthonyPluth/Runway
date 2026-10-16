<script lang="ts">
  import { api } from "$lib/api";
  import { act } from "$lib/act";
  import { refreshState } from "$lib/app.svelte";
  import { Button } from "$lib/components/ui/button";
  import { ConfirmDialog } from "$lib/components/ui/confirm-dialog";
  import Trash2 from "@lucide/svelte/icons/trash-2";
  import { toast } from "svelte-sonner";
  import type { Tx } from "./types";

  let { t, compact = false, onchanged, onclose }: { t: Tx; compact?: boolean; onchanged: () => void; onclose?: () => void } = $props();
  const name = $derived(t.payee || t.description || "this transaction");
  let deleting = $state(false);
  async function remove() {
    return act(async () => {
      await api(`/api/transactions/${encodeURIComponent(t.id)}`, { method: "DELETE" });
      toast.success(`Deleted ${name}`);
      refreshState(); onchanged(); onclose?.();
    });
  }
</script>

<Button variant="ghost" size="sm" class={compact ? "size-8 shrink-0 p-0 md:max-lg:hidden text-muted-foreground hover:text-destructive phone:size-11" : "self-start text-destructive hover:text-destructive"}
  aria-label={compact ? `Delete ${name}` : undefined} title="Delete transaction" onclick={() => (deleting = true)}>
  {#if compact}<Trash2 class="size-4" aria-hidden="true" />{:else}Delete transaction{/if}
</Button>
<ConfirmDialog bind:open={deleting} title={`Delete ${name}?`} destructive confirmLabel="Delete" busyLabel="Deleting…"
  description={`It leaves reports, budgets and the list. This can’t be undone.${t.source !== "manual" ? " A later bank sync may bring it back." : ""}`} onconfirm={remove} />
