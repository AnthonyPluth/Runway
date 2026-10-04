<script lang="ts">
  import { api } from "$lib/api";
  import { Button } from "$lib/components/ui/button";
  import { showTransactions } from "$lib/filters.svelte";
  import { fmtDate, fmtSigned } from "$lib/format";
  import type { Missed } from "$lib/types";
  import { toast } from "svelte-sonner";
  import type { Candidate } from "./types";
  import { act, errMsg } from "$lib/act";

  // "Link a transaction" on a missed payment: the unlinked transactions on its account around its date at about its
  // amount (GET /api/recurring/{id}/candidates). Linking one is the same link as Transactions' repeat button, and puts
  // the alert away, since you've said which payment it was (even one that came after its window). When none is close,
  // Transactions opens on that month to look further.
  let { m, onlinked }: { m: Missed; onlinked: () => void } = $props();
  let list = $state<Candidate[] | null>(null);
  let failed = $state(false);
  let busy = $state(false);

  async function load() {
    failed = false; list = null;
    try { list = await api<Candidate[]>(`/api/recurring/${m.recurring_id}/candidates?date=${m.date}`); }
    catch { failed = true; }
  }
  load();

  async function link(t: Candidate) {
    if (busy) return;
    busy = true;
    try {
      const r = await api<{ suggest_text?: string }>(`/api/transactions/${encodeURIComponent(t.id)}/recurring`, { method: "POST", body: { recurring_id: m.recurring_id } });
      // Linked; the alert going away is a nicety, so a failure there only means it may show again later.
      try { await api("/api/recurring/dismiss", { method: "POST", body: { key: m.key } }); } catch { /* see above */ }
      const text = r?.suggest_text;
      if (text) {
        toast.success(`Linked to ${m.name}`, { description: `Also match “${text}” from now on?`, action: { label: "Also match", onClick: () => alsoMatch(text) } });
      } else toast.success(`Linked to ${m.name}`);
      onlinked();
    } catch (err) { toast.error(errMsg(err)); }
    finally { busy = false; }
  }
  async function alsoMatch(text: string) {
    await act(async () => { await api(`/api/recurring/${m.recurring_id}/match`, { method: "POST", body: { text } }); toast.success(`${m.name} also matches “${text}” now`); });
  }
  function find(e: Event) {
    e.preventDefault();
    showTransactions({ account: m.account_id ?? "", month: m.date.slice(0, 7) });
  }
</script>

<div class="px-4 pb-3 text-sm sm:pl-15" aria-live="polite">
  {#if failed}
    <p class="text-muted-foreground">Couldn’t look for payments. <Button variant="link" size="sm" class="h-auto p-0" onclick={load}>Retry</Button></p>
  {:else if !list}
    <p class="text-muted-foreground" role="status">Looking for payments…</p>
  {:else if !list.length}
    <p class="text-muted-foreground">Nothing close to {fmtDate(m.date)}. <a class="font-medium text-primary" href="#transactions" onclick={find}>Look in Transactions</a></p>
  {:else}
    <ul class="flex flex-col" aria-label={`Payments that could be ${m.name}`}>
      {#each list as t (t.id)}
        <li class="flex min-h-10 items-center gap-3 border-t py-1 first:border-t-0">
          <span class="w-14 shrink-0 text-muted-foreground tabular-nums">{fmtDate(t.posted)}</span>
          <span class="min-w-0 flex-1 truncate">{t.name}</span>
          <span class={["shrink-0 tabular-nums", t.amount > 0 && "text-good"]}>{fmtSigned(t.amount)}</span>
          <Button variant="outline" size="sm" disabled={busy} aria-label={`Link ${t.name} on ${fmtDate(t.posted)}`} onclick={() => link(t)}>Link</Button>
        </li>
      {/each}
    </ul>
  {/if}
</div>
