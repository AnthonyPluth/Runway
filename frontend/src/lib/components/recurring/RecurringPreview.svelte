<script lang="ts">
  import { onDestroy } from "svelte";
  import { api } from "$lib/api";
  import { errMsg } from "$lib/act";
  import { fmt, fmtDow } from "$lib/format";
  import { accountName, type Account } from "$lib/types";
  import { previewGuidance } from "./schedule";
  import type { RecurringValues } from "./types";

  // The Add form's "Next dates": the next three times the draft would come (the one date of a one-time item), as the
  // forecast would have them, moved off weekends and bank holidays. The dates come from the server (POST
  // /api/recurring/preview, the forecast's own schedule); until the draft's schedule is complete this says what is missing
  // instead of showing dates.
  let { v, out, account }: { v: RecurringValues; out: boolean; account?: Account } = $props();

  const guidance = $derived(previewGuidance(v));
  let dates = $state<string[]>([]);
  let failed = $state<string | null>(null);
  let waiting = $state(false);
  let timer: ReturnType<typeof setTimeout> | undefined;
  let asked = 0;   // a slower, older answer never replaces a newer one

  $effect(() => {
    // Everything the schedule reads, so a change to any of it asks again.
    const body = { frequency: v.frequency, dates: v.dates, anchor_date: v.anchor_date, end_date: v.end_date, amount: v.amount };
    clearTimeout(timer);
    const n = ++asked;
    if (guidance) { dates = []; failed = null; waiting = false; return; }
    waiting = true;
    timer = setTimeout(async () => {
      try {
        const r = await api<{ dates?: string[] }>("/api/recurring/preview", { method: "POST", body, background: true });
        if (n !== asked) return;
        dates = r?.dates ?? []; failed = r?.dates ? null : "Couldn’t work out the dates.";
      } catch (e) {
        if (n !== asked) return;
        dates = []; failed = errMsg(e);
      } finally { if (n === asked) waiting = false; }
    }, 250);
  });
  onDestroy(() => { clearTimeout(timer); asked++; });

  const amount = $derived(Math.abs(Number(v.amount)));
  const once = $derived(v.frequency === "once");
</script>

<section class="mt-4 rounded-md border border-dashed border-border px-3 py-2 text-sm" aria-labelledby="preview-title" data-preview>
  <h3 id="preview-title" class="mb-1 text-xs font-medium text-muted-foreground">{once ? "When it comes" : "Next dates"}</h3>
  {#if guidance}
    <p class="text-muted-foreground">Dates show once the schedule is complete. {guidance}</p>
  {:else if failed}
    <p class="text-destructive" role="alert">{failed}</p>
  {:else if dates.length}
    <ul class="flex flex-col gap-0.5" class:opacity-60={waiting}>
      {#each dates as d (d)}
        <li class="flex flex-wrap items-baseline gap-x-2">
          <span class="font-medium">{fmtDow(d)}</span>
          <span class="tabular-nums">{out ? "−" : "+"}{fmt(amount)}</span>
          <span class="text-muted-foreground">{out ? "Money out" : "Money in"}{account ? ` · ${accountName(account)}` : ""}</span>
        </li>
      {/each}
    </ul>
  {:else if waiting}
    <p class="text-muted-foreground">Working out the dates…</p>
  {:else}
    <p class="text-muted-foreground">No dates come up from this schedule.</p>
  {/if}
</section>
