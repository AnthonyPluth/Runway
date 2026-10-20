<script lang="ts">
  import { api } from "$lib/api";
  import { act } from "$lib/act";
  import CatIcon from "$lib/components/CatIcon.svelte";
  import Logo from "$lib/components/Logo.svelte";
  import MissedAlert from "$lib/components/MissedAlert.svelte";
  import { Button } from "$lib/components/ui/button";
  import { fmtDate, fmtSigned, isoDay, plural } from "$lib/format";
  import type { Missed } from "$lib/types";
  import { undoable } from "$lib/undo";
  import ChevronDown from "@lucide/svelte/icons/chevron-down";
  import LinkPicker from "./LinkPicker.svelte";
  import RecIcon from "./RecIcon.svelte";
  import type { RecurringItem } from "./types";

  // A recurring item that missed more than one payment, as one row of Needs attention: how many since when, "Link…" (the
  // link picker for the latest of them), "Skip all N" (each a one-off $0, in one request; one Undo puts back what each
  // date had) and, once it has missed three or more in a row (`r.missed_run`), "Mark as ended", which ends it the day
  // before the first of those, so the forecast and Upcoming stop expecting it (Undo puts its end date back). "Show each"
  // lists them as single rows, to link or skip one on its own. `list`: its missed payments still listed, newest first.
  // `ondone`/`onundone` tell the page which keys went or came back; `onchanged` loads the page again after an end date.
  let { r, list, today = isoDay(), ondone, onundone, onchanged }: {
    r: RecurringItem; list: Missed[]; today?: string; ondone: (keys: string[]) => void; onundone: (keys: string[]) => void;
    onchanged: () => Promise<void>;
  } = $props();
  let picking = $state(false);
  let showEach = $state(false);
  let busy = $state(false);

  const latest = $derived(list[0]);
  const oldest = $derived(list[list.length - 1]);
  // Every missed payment of the run is still listed: one skipped or linked here since would have ended the run.
  const run = $derived(r.missed_run ?? []);
  const canEnd = $derived(run.length >= 3 && run.every((k) => list.some((m) => m.key === k)));
  const runStart = $derived(list.find((m) => m.key === run[0])?.date ?? oldest.date);

  async function skipAll() {
    await act(async () => {
      const res = await api<{ keys: string[]; previous: { key: string; amount: number | null }[] }>(`/api/recurring/${r.id}/skip-missed`, { method: "POST", body: {} });
      picking = false; ondone(res.keys);
      undoable(`Skipped ${plural(res.keys.length, "missed payment")} of ${r.name}`, async () => {
        await api(`/api/recurring/${r.id}/skip-missed`, { method: "POST", body: { restore: res.previous } });
        onundone(res.keys);
      });
    }, { busy: (on) => (busy = on) });
  }
  async function markEnded() {
    await act(async () => {
      const res = await api<{ end_date: string; previous: { end_date: string | null } }>(`/api/recurring/${r.id}/end`, { method: "POST", body: {} });
      await onchanged();
      undoable(`Marked ${r.name} as ended`, async () => {
        await api(`/api/recurring/${r.id}/end`, { method: "POST", body: { restore: res.previous } });
        await onchanged();
      }, { description: `Not expected after ${fmtDate(res.end_date)}` });
    }, { busy: (on) => (busy = on) });
  }
</script>

<div data-missed-group={r.id}>
  <div class="cell flex-wrap">
    <span class="shrink-0" aria-hidden="true">
      {#if r.logo}<Logo src={r.logo} size={28} />
      {:else if r.last_matched?.category}<CatIcon name={r.last_matched.category} size={28} />
      {:else}<RecIcon id={r.account_id} />{/if}
    </span>
    <div class="min-w-0 flex-1">
      <p class="flex gap-2 text-[15px]"><span class="min-w-0 truncate font-medium">{r.name}</span><span class="shrink-0 tabular-nums">{fmtSigned(latest.amount)}</span></p>
      <p class="truncate text-[13px] text-muted-foreground"><span class="text-warning">{list.length} missed</span> since {fmtDate(oldest.date)}{r.account_name ? ` · ${r.account_name}` : ""}</p>
      <button type="button" class="mt-0.5 inline-flex cursor-pointer items-center gap-1 text-[13px] font-medium text-primary" aria-expanded={showEach}
        onclick={() => (showEach = !showEach)}>{showEach ? "Hide each" : "Show each"}<ChevronDown class={`size-3.5 transition-transform ${showEach ? "rotate-180" : ""}`} aria-hidden="true" /></button>
    </div>
    <span class="flex shrink-0 flex-wrap gap-1 max-lg:basis-full max-lg:pl-10">
      <Button variant="ghost" size="sm" class="text-primary" aria-expanded={picking} aria-label={`Link a transaction to ${r.name} on ${fmtDate(latest.date)}`}
        onclick={() => (picking = !picking)}>Link…</Button>
      <Button variant="ghost" size="sm" class="text-muted-foreground" disabled={busy} onclick={skipAll}>Skip all {list.length}</Button>
      {#if canEnd}
        <Button variant="ghost" size="sm" class="text-muted-foreground" disabled={busy} title={`Ends it the day before ${fmtDate(runStart)}, so it isn’t expected any more`}
          onclick={markEnded}>Mark as ended</Button>
      {/if}
    </span>
  </div>
  {#if picking}{#key latest.key}<LinkPicker m={latest} onlinked={() => { picking = false; ondone([latest.key]); }} />{/key}{/if}
  {#if showEach}
    <div class="border-t border-border/60" role="list" aria-label={`Each missed payment of ${r.name}`}>
      {#each list as m (m.key)}
        <div role="listitem"><MissedAlert {m} {today} ondone={(k) => ondone([k])} onundone={(k) => onundone([k])} /></div>
      {/each}
    </div>
  {/if}
</div>
