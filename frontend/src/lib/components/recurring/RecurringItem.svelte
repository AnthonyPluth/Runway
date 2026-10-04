<script lang="ts">
  import { api } from "$lib/api";
  import { app, reload } from "$lib/app.svelte";
  import AcctLabel from "$lib/components/AcctLabel.svelte";
  import { Badge } from "$lib/components/ui/badge";
  import { Button } from "$lib/components/ui/button";
  import { ConfirmDialog } from "$lib/components/ui/confirm-dialog";
  import { fmt, fmtDate, fmtSigned, isoDay, nb, plural } from "$lib/format";
  import type { Account } from "$lib/types";
  import { undoable } from "$lib/undo";
  import { toast } from "svelte-sonner";
  import { tick, untrack } from "svelte";
  import ChevronRight from "@lucide/svelte/icons/chevron-right";
  import CatIcon from "$lib/components/CatIcon.svelte";
  import Logo from "$lib/components/Logo.svelte";
  import LogoPicker from "$lib/components/transactions/LogoPicker.svelte";
  import RecIcon from "./RecIcon.svelte";
  import RecurringFields from "./RecurringFields.svelte";
  import { dueLabel } from "./schedule";
  import { FREQ, validate, type MatchedTx, type RecurringItem, type RecurringValues } from "./types";
  import { act, errMsg } from "$lib/act";

  // One recurring item, a row of its Money in or Money out group: a summary line that opens into its fields, each saved
  // as you change it, and Pause, Skip the next one and Remove. After a save, `onsaved` gets the fresh list, so the summary
  // (amount, when it's due, matches) and its group catch up. `focus`: opened from a link to this item (an upcoming item's
  // name): it comes into view. `missed`: how many payments it missed that are still listed under Needs attention.
  let { r, accounts, open, focus = false, missed = 0, today = isoDay(), ontoggle, onsaved }: {
    r: RecurringItem; accounts: Account[]; open: boolean; focus?: boolean; missed?: number; today?: string;
    ontoggle: (open: boolean) => void; onsaved?: (items: RecurringItem[]) => void;
  } = $props();
  function intoView(el: HTMLElement) { if (focus) el.scrollIntoView?.({ block: "center" }); }

  const init = (): RecurringValues => ({
    name: r.name, account_id: r.account_id, amount: r.amount, amount_mode: r.amount_mode || "fixed", frequency: r.frequency,
    dates: r.dates || "", anchor_date: r.anchor_date || "", match: r.match || "", amount_min: r.amount_min ?? "", amount_max: r.amount_max ?? "",
    end_date: r.end_date || "",
  });
  let v: RecurringValues = $state(init());
  // Open or closed is yours to change; `open` only says how it starts.
  let isOpen = $state(untrack(() => open));
  let active = $state(untrack(() => !!r.active));
  let attempted = $state(false);   // once a save was refused, the fields that need fixing say so
  const errors = $derived(attempted ? validate(v) : {});
  let matches = $state<MatchedTx[] | null>(null);
  // How each matched transaction got here; nothing for links from before Runway kept it.
  const LINKED_BY: Record<string, string> = { you: "linked by you", auto: "matched automatically" };

  const amt = $derived(r.expected_amount ?? r.amount);
  const due = $derived(dueLabel({ ...r, active: active ? 1 : 0 }, today));
  // A fixed amount the last payments all missed: what they came to, on the row ("Usually $87.40") and as "Use $87.40" inside.
  const usually = $derived((v.amount_mode || "fixed") === "fixed" && r.suggested_amount ? Math.abs(r.suggested_amount) : null);

  async function refresh() {
    if (!onsaved) return;
    try { onsaved(await api<RecurringItem[]>("/api/recurring")); }
    catch { /* saved; the summary catches up on the next load */ }
  }

  // The logo picker chooses by the item's name (the one the logo comes from, else its last matched transaction's), the
  // same choice Transactions keeps by merchant name. A new one shows here and in Upcoming, so reload both.
  async function logoChanged() { reload(); await refresh(); }

  // Everything as it is now, to the server. Nothing saves until the fields are complete: throwing (rather than
  // returning) keeps "Saved ✓" from flashing.
  async function persist() {
    const first = Object.values(validate(v))[0];
    attempted = !!first;
    if (first) throw new Error(`Not saved yet. ${first}`);
    const res = await api<{ linked: number; amount_min?: number | null; amount_max?: number | null }>(`/api/recurring/${r.id}`, { method: "POST", body: { ...v, active: active ? 1 : 0 } });
    if (res.linked) toast(`Saved · matched ${res.linked} more`);
    // A new amount outside the range moves the range with it (the server says where to), so the fields show that.
    if (res.amount_min !== undefined) { v.amount_min = res.amount_min ?? ""; v.amount_max = res.amount_max ?? ""; }
    await refresh();
  }
  type Field = HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement;
  async function save(f: Field) {
    await tick();   // let the field's binding catch up with the change first
    const errs = validate(v);
    if (errs.dates && f.name === "frequency") f.closest("details")?.querySelector<HTMLInputElement>("input[name=dates]")?.focus();
    await persist();
  }

  // Pause takes it out of the forecast (and out of matching and missed payments) until you resume it; Undo resumes.
  let pausing = $state(false);
  async function setActive(next: boolean) {
    if (pausing) return;
    pausing = true;
    const was = active;
    active = next;
    try { await persist(); }
    catch (err) { active = was; toast.error(errMsg(err)); return; }
    finally { pausing = false; }
    if (next) toast.success(`${v.name} is back in the forecast`);
    else undoable(`Paused ${v.name}`, async () => {
      active = true;
      try { await persist(); } catch (err) { active = false; throw err; }
    });
  }

  // Skipping one date is a one-off $0 for it (the same as editing that date's amount to $0 on Overview): the forecast
  // leaves it out, and it isn't missed. Taking it back is the edit's reset.
  const overrides = (key: string, method: "POST" | "DELETE") => api("/api/overrides", { method, body: method === "POST" ? { key, amount: 0 } : { key } });
  async function skipNext() {
    const d = r.next_date;
    if (!d) return;
    const key = `rec:${r.id}:${d}`;
    if (!(await act(async () => { await overrides(key, "POST"); }))) return;
    await refresh();
    undoable(`Skipped ${v.name} on ${fmtDate(d)}`, async () => { await overrides(key, "DELETE"); await refresh(); });
  }
  async function unskip(d: string) {
    await act(async () => { await overrides(`rec:${r.id}:${d}`, "DELETE"); toast.success(`${fmtDate(d)} is back in the forecast`); await refresh(); });
  }

  // Removing says what it does first: the matched transactions are unlinked (they stay in your history) and its one-off
  // changes to single dates go.
  let removing = $state(false);
  async function remove(): Promise<boolean> {
    return act(async () => { await api(`/api/recurring/${r.id}`, { method: "DELETE" }); toast("Removed"); reload(); });
  }
  async function showMatches() {
    if (matches) { matches = null; return; }
    await act(async () => { matches = (await api<{ items: MatchedTx[] }>(`/api/transactions?recurring=${r.id}&limit=50`)).items; });
  }
</script>

<!-- One child of the group's list, so its hairline falls above the row (the dialog renders elsewhere). -->
<div>
<details class="group/item" data-recurring={r.id} use:intoView bind:open={isOpen} ontoggle={(e) => ontoggle(e.currentTarget.open)}>
  <summary class="cell cursor-pointer list-none transition-colors hover:bg-white/4 group-open/item:bg-white/3 [&::-webkit-details-marker]:hidden">
    <!-- A button inside the summary doesn't toggle it. Its account's bank is a small badge on the logo, as on
         Transactions; an item shown by its bank's own mark (no logo or category yet), or without an account, has none. -->
    <span class="relative shrink-0">
      {#if (r.logo || r.last_matched?.category) && r.account_id && app.state?.brands?.[r.account_id]}
        <span class="pointer-events-none absolute -right-1.5 -bottom-1.5 z-[1] rounded-md" title={r.account_name || undefined}
          data-account-badge><AcctLabel id={r.account_id} name={r.account_name ?? ""} iconClass="size-4!" labelClass="hidden" /></span>
      {/if}
      <LogoPicker name={r.name} onchanged={logoChanged}>
        {#if r.logo}<Logo src={r.logo} size={28} />
        {:else if r.last_matched?.category}<CatIcon name={r.last_matched.category} size={28} />
        {:else}<RecIcon id={r.account_id} />{/if}
      </LogoPicker>
    </span>
    <span class="flex min-w-0 flex-1 flex-col gap-0.5">
      <span class="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5 text-[15px] font-medium">
        <span class="min-w-0 truncate">{v.name || r.name}</span>
        {#if !active}<Badge variant="secondary">Paused</Badge>{/if}
        {#if usually && active}<Badge variant="outline" class="font-normal text-muted-foreground tabular-nums" title={`The last payments came to about ${fmt(usually)}`}>Usually {fmt(usually)}</Badge>{/if}
      </span>
      <span class="text-[13px] text-muted-foreground">
        {nb(FREQ[v.frequency] || v.frequency)}{#if due?.late}{" · "}<span class="text-warning">{nb(due.text)}</span>{:else if due}{" · "}{nb(due.text)}{/if}{#if missed}{" · "}<span class="text-warning">{nb(missed === 1 ? "missed a payment" : `missed ${missed}`)}</span>{/if}{#if r.matched_count}{" · "}{nb(`${r.matched_count} matched`)}{/if}
      </span>
    </span>
    <span class={["shrink-0 text-[15px] font-medium tabular-nums", amt > 0 && "text-good"]}>{fmtSigned(amt)}</span>
    <ChevronRight class="size-4 shrink-0 text-muted-foreground transition-transform group-open/item:rotate-90" aria-hidden="true" />
  </summary>
  <div class="px-4 pt-3 pb-5 sm:pl-[3.75rem]">
    <RecurringFields bind:v {accounts} {save} {errors} suggested={r.suggested_amount} suggestedFor={r.amount} next={active ? r.next_date : null} />
    {#each r.skipped ?? [] as d (d)}
      <p class="mt-3 text-sm text-muted-foreground">Skipping {fmtDate(d)} ·
        <Button variant="link" size="sm" class="h-auto p-0" onclick={() => unskip(d)}>Put it back</Button></p>
    {/each}
    <div class="mt-4 flex flex-wrap items-center gap-2">
      <Button variant="outline" size="sm" disabled={pausing} onclick={() => setActive(!active)}>{active ? "Pause" : "Resume"}</Button>
      {#if active && r.next_date && r.frequency !== "once"}
        <Button variant="outline" size="sm" title={`Leave out ${fmtDate(r.next_date)} only`} onclick={skipNext}>Skip the next one</Button>
      {/if}
      {#if r.matched_count}
        <Button variant="link" size="sm" class="h-auto px-1" aria-expanded={!!matches} onclick={showMatches}>
          {matches ? "Hide matched transactions" : "Show matched transactions"}
        </Button>
      {/if}
      <Button variant="link" size="sm" class="ml-auto h-auto px-0" onclick={() => (removing = true)}>Remove</Button>
    </div>
    {#if matches}
      {#if matches.length}
        <div class="mt-3 overflow-x-auto">
          <table class="w-full text-sm">
            <tbody>
              {#each matches as t (t.id)}
                <tr class="border-t [&>td]:py-1.5">
                  <td class="w-20 whitespace-nowrap text-muted-foreground">{fmtDate(t.posted)}</td>
                  <td class="px-2">{t.description}</td>
                  <td class="px-2 text-xs whitespace-nowrap text-muted-foreground">{LINKED_BY[t.recurring_linked_by ?? ""] ?? ""}</td>
                  <td class="text-right whitespace-nowrap tabular-nums">{fmt(t.amount)}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {:else}
        <p class="mt-3 text-sm text-muted-foreground">No matched transactions.</p>
      {/if}
    {/if}
  </div>
</details>

<ConfirmDialog bind:open={removing} destructive title={`Remove ${r.name}?`} confirmLabel="Remove" busyLabel="Removing…" onconfirm={remove}
  description={`${r.matched_count ? `This unlinks ${plural(r.matched_count, "matched transaction")}; they stay in your history.` : "No transactions are linked to it."} Any one-off changes you made to its dates are cleared too.`} />
</div>
