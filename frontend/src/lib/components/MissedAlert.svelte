<script lang="ts">
  import { api } from "$lib/api";
  import LinkPicker from "$lib/components/recurring/LinkPicker.svelte";
  import { lateBy } from "$lib/components/recurring/schedule";
  import { Button } from "$lib/components/ui/button";
  import { fmtDate, fmtSigned, isoDay } from "$lib/format";
  import type { Missed } from "$lib/types";
  import { undoable } from "$lib/undo";
  import { toast } from "svelte-sonner";
  import TriangleAlert from "@lucide/svelte/icons/triangle-alert";

  // A recurring payment that hasn't shown up (a row of a Needs attention list, on Overview and Recurring): how late it
  // is, "Link a transaction" to say which payment it was (LinkPicker, right here), or "Skip this one", a one-off $0 for
  // that date, which the forecast and Recurring read as skipped (Undo takes it back). `ondone`/`onundone` tell the page.
  let { m, today = isoDay(), ondone, onundone }: { m: Missed; today?: string; ondone?: (key: string) => void; onundone?: (key: string) => void } = $props();
  let gone = $state(false);
  let picking = $state(false);

  async function skip() {
    try {
      await api("/api/overrides", { method: "POST", body: { key: m.key, amount: 0 } });
      gone = true; ondone?.(m.key);
      undoable(`Skipped ${m.name} on ${fmtDate(m.date)}`, async () => {
        await api("/api/overrides", { method: "DELETE", body: { key: m.key } });
        gone = false; picking = false; onundone?.(m.key);
      });
    } catch (err) { toast.error((err as Error).message); }
  }
  function linked() { gone = true; ondone?.(m.key); }
</script>

{#if !gone}
  <div data-missed={m.key}>
    <div class="cell flex-wrap">
      <span class="flex size-8 shrink-0 items-center justify-center rounded-lg bg-warning text-black" aria-hidden="true"><TriangleAlert class="size-4" /></span>
      <div class="min-w-0 flex-1">
        <p class="flex gap-2 text-[15px]"><span class="min-w-0 truncate font-medium">{m.name}</span><span class="shrink-0 tabular-nums">{fmtSigned(m.amount)}</span></p>
        <p class="truncate text-[13px] text-muted-foreground"><span class="text-warning">{lateBy(m.date, today)}</span> · due {fmtDate(m.date)}{m.account_name ? ` · ${m.account_name}` : ""}</p>
      </div>
      <span class="flex shrink-0 gap-1 max-lg:basis-full max-lg:pl-8">
        {#if m.recurring_id}
          <Button variant="ghost" size="sm" class="text-primary" aria-expanded={picking} onclick={() => (picking = !picking)}>Link a transaction</Button>
        {/if}
        <Button variant="ghost" size="sm" class="text-muted-foreground" onclick={skip}>Skip this one</Button>
      </span>
    </div>
    {#if picking && m.recurring_id}<LinkPicker {m} onlinked={linked} />{/if}
  </div>
{/if}
