<script lang="ts">
  import { api } from "$lib/api";
  import { app } from "$lib/app.svelte";
  import AmountEdit from "$lib/components/AmountEdit.svelte";
  import BankBadge from "$lib/components/BankBadge.svelte";
  import CatIcon from "$lib/components/CatIcon.svelte";
  import Logo from "$lib/components/Logo.svelte";
  import { Badge } from "$lib/components/ui/badge";
  import { Button } from "$lib/components/ui/button";
  import { fmt, fmtDate, fmtDow, fmtSigned } from "$lib/format";
  import ChevronRight from "@lucide/svelte/icons/chevron-right";
  import ChevronUp from "@lucide/svelte/icons/chevron-up";
  import type { ForecastEvent } from "$lib/types";
  import { undoable } from "$lib/undo";
  import { toast } from "svelte-sonner";
  import EstimateBreakdown from "./EstimateBreakdown.svelte";
  import { estimateTitle } from "./estimate";

  // What's coming up. Click an amount to change just that one occurrence; a recurring item's name opens it in Recurring, to
  // change every one. A churning card's annual fee (kind "fee") says which card payment it's in, instead of a balance.
  // `limit` is how many show before
  // "Show all"; `accounts` adds each one's account (Transactions shows several accounts' items together). After an amount
  // changes, `onchanged` loads the page's forecast again (in place: the page isn't drawn afresh).
  let { events, limit = 8, accounts = false, all = $bindable(false), onchanged }: {
    events: (ForecastEvent & { late_from?: string | null })[];
    limit?: number; accounts?: boolean; all?: boolean; onchanged: () => void;
  } = $props();
  const shown = $derived(all ? events : events.slice(0, limit));
  // Estimated statements whose breakdown is open under the row (by the row's id): its asterisk toggles it.
  let explained = $state<Record<string, boolean>>({});
  const uid = $props.id();
  // A day at a time, under its date, with no dividers inside a day. A bank settles a day's payments together, so the
  // projected balance shows once a day for each account, under the day's items: the balance after its last item that day
  // (an annual fee or a recurring charge on a card is a card charge: it never moves one).
  const days = $derived.by(() => {
    type Ev = (typeof events)[number];
    const out: { date: string; rows: { e: Ev; i: number }[]; balances: { account?: string | null; amount: number }[] }[] = [];
    shown.forEach((e, i) => {
      if (out.at(-1)?.date !== e.date) out.push({ date: e.date, rows: [], balances: [] });
      out.at(-1)!.rows.push({ e, i });
    });
    for (const d of out) {
      const last = new Map<string, Ev>();
      for (const { e } of d.rows) if (e.kind !== "fee" && e.balance_after != null) last.set(e.account_id ?? "", e);
      d.balances = [...last.values()].map((e) => ({ account: e.account, amount: e.balance_after ?? 0 }));
    }
    return out;
  });

  // What a recurring item had before its own amount changed (POST /api/overrides, /api/recurring/{id}/amount), for Undo.
  type Previous = { amount: number; amount_mode: string | null; amount_since: string | null; amount_min: number | null; amount_max: number | null };
  const restoreItem = (id: number, previous: Previous) => api(`/api/recurring/${id}/amount`, { method: "POST", body: { restore: previous } });

  // A changed amount is for that date only, with Undo; for a repeating item, "From now on" makes it the item's amount
  // instead. A one-time item has only the one date, so the server changes the item itself (Recurring shows it).
  async function change(e: ForecastEvent, value: number) {
    // An edit is what the occurrence comes to in all: on "the rest" of one paid in parts, what's paid so far is added.
    const sign = e.amount < 0 ? -1 : 1;
    const total = Math.round(sign * (value + Math.abs(e.paid_so_far ?? 0)) * 100) / 100;
    const key = e.key!;
    // This date's own edit as it was (a total, like this one), to put back on Undo; none if it had none.
    const before = e.overridden ? Math.round((e.amount + (e.paid_so_far ?? 0)) * 100) / 100 : null;
    let r: { item?: { id: number; name: string }; previous?: Previous };
    try { r = await api("/api/overrides", { method: "POST", body: { key, amount: total } }); }
    catch (err) { toast.error((err as Error).message); return; }
    onchanged();
    if (r?.item && r.previous) {
      const { item, previous } = r;
      undoable(`${item.name} is ${fmt(Math.abs(total))} now`, async () => { await restoreItem(item.id, previous); onchanged(); });
      return;
    }
    const day = key.startsWith("rec:") ? key.split(":")[2] : e.date;
    const putBack = () => api("/api/overrides", before === null ? { method: "DELETE", body: { key } } : { method: "POST", body: { key, amount: before } });
    const rid = e.recurring_id;
    undoable(`Changed for ${fmtDate(day)} only`, async () => { await putBack(); onchanged(); },
      rid ? { also: { label: "From now on", run: () => fromNowOn(rid, e.name, key, total) } } : {});
  }
  // "From now on": the new amount becomes the item's (a fixed amount, if it was learned from the payments), and this
  // date's edit goes. Undo puts the item back as it was, and the date's edit with it.
  async function fromNowOn(id: number, name: string, key: string, total: number) {
    const { previous } = await api<{ previous: Previous }>(`/api/recurring/${id}/amount`, { method: "POST", body: { amount: total, key } });
    onchanged();
    undoable(`${name} is ${fmt(Math.abs(total))} from now on`, async () => {
      await restoreItem(id, previous);
      await api("/api/overrides", { method: "POST", body: { key, amount: total } });
      onchanged();
    }, { description: previous.amount_mode && previous.amount_mode !== "fixed" ? "A fixed amount now, not one from recent payments" : undefined });
  }
  async function reset(e: ForecastEvent) {
    try { await api("/api/overrides", { method: "DELETE", body: { key: e.key } }); toast.success("Back to the usual amount"); onchanged(); }
    catch (err) { toast.error((err as Error).message); }
  }
</script>

<!-- Rows of a grouped list (the caller puts them in a Group). -->
{#if !events.length}
  <p class="cell text-sm text-muted-foreground">Nothing scheduled. Add paychecks and bills on&nbsp;<a class="font-medium text-primary" href="#recurring">Recurring</a>.</p>
{:else}
  {#each days as d (d.date)}
  <!-- One block a day: the group's hairlines fall between days, not between a day's items. -->
  <div data-day={d.date}>
  <div class="px-4 pt-2.5 pb-1 text-[13px] font-medium text-muted-foreground" role="heading" aria-level="3">{fmtDow(d.date)}</div>
  {#each d.rows as { e, i } (e.key ?? `${e.date}-${e.name}-${i}`)}
    {@const bank = e.kind === "card" && e.card_id ? app.state?.brands?.[e.card_id] : undefined}
    {@const rid = e.key ?? `${e.date}-${e.name}-${i}`}
    {@const open = !!(e.estimate && !e.overridden && explained[rid])}
    <!-- An estimate's breakdown, opened, takes a line of its own under the name (the icon and amount stay with the name). -->
    <div class={["cell min-h-12 flex-wrap py-2", open && "items-start"]}>
      <!-- Logos as they are, with nothing behind them, as in Transactions; with several accounts' items together, the
           item's account is its bank on the logo's corner, as there, not a line of its own. -->
      <span class="relative shrink-0">
        {#if e.logo}
          <Logo src={e.logo} />
        {:else if bank?.src}
          <img class="size-8 shrink-0 rounded-lg object-contain" src={bank.src} alt="" title={bank.institution ?? ""} loading="lazy" width="32" height="32" />
        {:else}
          <CatIcon name={e.kind === "card" ? "Credit Card Payment" : e.category} size={32} />
        {/if}
        {#if accounts && e.account_id}<BankBadge accountId={e.account_id} name={e.account ?? ""} />{/if}
      </span>
      <div class="min-w-0 flex-1">
        <div class="flex flex-wrap items-center gap-1.5 text-[15px]">
          {#if e.recurring_id}
            <!-- A recurring item's name opens it in Recurring, to change every one. -->
            <a class="truncate hover:underline" href={`#recurring?item=${e.recurring_id}`} title="Open in Recurring">{e.name}</a>
          {:else}<span class="truncate">{e.name}</span>{/if}
          {#if e.paid_so_far}<Badge variant="secondary" title={`${fmt(Math.abs(e.paid_so_far))} has ${e.amount > 0 ? "come in" : "gone out"} already; this is the rest`}>rest</Badge>{/if}
          {#if e.late_from}<Badge variant="secondary" title={`Was due ${e.late_from} and ${e.paid_so_far ? "the rest " : ""}hasn't shown up yet`}>late</Badge>{/if}
          <!-- A recurring date edited to $0 is one you skipped (Recurring's "Skip the next one"); reset puts it back. -->
          {#if e.overridden}<Badge variant="secondary" title={`Usually ${fmt(e.original_amount)}`}>{e.recurring_id && Math.abs(e.amount) < 0.005 ? "skipped" : "edited"}</Badge>{/if}
        </div>
        {#if e.kind === "fee"}
          <!-- An annual fee is a charge on its card: it reaches cash in the card's statement payment, not on its own. -->
          <div class="truncate text-[13px] text-muted-foreground tabular-nums"
            title="Charged to the card: it's paid with the card's statement, so it's in that payment rather than taken out of your balance on its own">
            {#if !e.account}card not linked to an account, so not in the forecast
            {:else if e.paid_on}on {e.account}, paid with its {fmtDate(e.paid_on)} payment
            {:else}on {e.account}; its payment isn’t in the forecast{/if}
          </div>
        {/if}
      </div>
      <div class={["flex shrink-0 flex-col items-end text-[15px] tabular-nums", e.amount > 0 && "text-good"]}>
        <!-- An estimate is marked with an asterisk after its amount; what it's based on is in its tooltip. A card statement's
             asterisk is a button: its tooltip lists what the estimate is made of, and a tap shows the same under the row.
             An amount you've changed is yours, not an estimate: no asterisk. -->
        <!-- The asterisk hangs past the amount, so amounts line up on the right with or without one. -->
        <span class="relative flex items-baseline">{#if e.key}<AmountEdit amount={e.amount} signed label="Amount" title="Change this amount for this date only"
            save={(v) => change(e, v)} />{:else}<span class={e.amount > 0 ? "font-semibold" : undefined}>{fmtSigned(e.amount)}</span>{/if}{#if e.estimated && !e.overridden}{#if e.estimate}{@const est = e.estimate}<button type="button"
            class="absolute top-0 left-full ml-0.5 cursor-pointer text-muted-foreground after:absolute after:-inset-y-3 after:left-0 after:-right-3 hover:text-foreground"
            aria-label="What this estimate is made of" aria-expanded={open} aria-controls={`${uid}-${rid}`}
            title={estimateTitle(est)} onclick={() => (explained[rid] = !explained[rid])}>*</button>{:else}<span class="absolute top-0 left-full ml-0.5 cursor-help text-muted-foreground" role="img" aria-label="estimate" title={`Estimate: ${e.kind !== "card" ? "based on recent payments"
              : "the statement hasn't closed yet; what's on the card so far, plus your budgets paid with it and its recurring charges"}`}>*</span>{/if}{/if}</span>
        {#if e.overridden}
          <Button variant="link" size="sm" class="h-auto p-0 text-xs" title="Go back to the usual amount" onclick={() => reset(e)}>reset</Button>
        {/if}
      </div>
      {#if open && e.estimate}
        <!-- under the name (past the icon), its amounts lined up under the row's -->
        <EstimateBreakdown estimate={e.estimate} id={`${uid}-${rid}`} class="-mt-2 basis-full pl-11" />
      {/if}
    </div>
  {/each}
  {#each d.balances as b, k (k)}
    <div class="flex flex-wrap justify-end gap-x-1.5 px-4 pt-0.5 pb-3 text-[13px] text-muted-foreground tabular-nums">
      {#if accounts && b.account}<span class="truncate">{b.account} ·</span>{/if}
      <span class={b.amount < 0 ? "font-medium text-destructive" : ""}>projected balance {fmt(b.amount)}</span>
    </div>
  {/each}
  </div>
  {/each}
  {#if events.length > shown.length}
    <button type="button" class="cell justify-between text-[15px] text-primary" onclick={() => (all = true)}>
      Show all {events.length}<ChevronRight class="size-4 text-muted-foreground" aria-hidden="true" />
    </button>
  {/if}
  {#if all && events.length > limit}
    <button type="button" class="cell justify-between text-[15px] text-primary" onclick={() => (all = false)}>
      Show fewer<ChevronUp class="size-4 text-muted-foreground" aria-hidden="true" />
    </button>
  {/if}
{/if}
