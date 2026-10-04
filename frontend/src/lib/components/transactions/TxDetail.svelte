<script lang="ts">
  import { api } from "$lib/api";
  import { app, refreshState } from "$lib/app.svelte";
  import { categories } from "$lib/categories.svelte";
  import AcctLabel from "$lib/components/AcctLabel.svelte";
  import CategorySelect from "$lib/components/CategorySelect.svelte";
  import OrderDetail from "$lib/components/orders/OrderDetail.svelte";
  import type { RecurringItem } from "$lib/components/recurring/types";
  import { Badge } from "$lib/components/ui/badge";
  import { Button } from "$lib/components/ui/button";
  import { ConfirmDialog } from "$lib/components/ui/confirm-dialog";
  import { Input } from "$lib/components/ui/input";
  import * as Sheet from "$lib/components/ui/sheet";
  import { Segmented } from "$lib/components/ui/toggle-group";
  import { fmt, fmtDate, fmtSigned } from "$lib/format";
  import { undoable } from "$lib/undo";
  import { cn } from "$lib/utils";
  import { toast } from "svelte-sonner";
  import RecurringPicker from "./RecurringPicker.svelte";
  import { restoreTx, type KeepBank, type Was } from "./restore";
  import SplitEditor from "./SplitEditor.svelte";
  import Switch from "./Switch.svelte";
  import type { Tx } from "./types";
  import { sourceLabel } from "./sources";
  import { act, errMsg } from "$lib/act";

  // One transaction in the sheet: its name, category, date, amount and note, each saved when you leave it (or pick
  // one), with an Undo. A bank's date and amount you changed stay yours (the bank's is a hover away), and a pending one's
  // are the bank's until it posts. Below: whether it counts, where it came from, and its split, recurring item and receipt.
  // `onsave` saves a category as the row does (false when it couldn't be); `onchanged` loads the list again.
  // `onpatched`: the transaction as an edit left it, for the sheet to show if the list no longer has it.
  let { t, recurring, family, onsave, onchanged, onclose, onpatched }: {
    t: Tx; recurring: RecurringItem[]; family?: string[];
    onsave: (t: Tx, category: string) => Promise<boolean | void>; onchanged: () => void; onclose: () => void;
    onpatched?: (t: Partial<Tx>) => void;
  } = $props();

  const manual = $derived(t.source === "manual");
  const locked = $derived(!!t.pending && !manual);   // the bank still decides a pending one's date and amount
  const name = $derived(t.payee || t.description || "");
  const split = $derived(!!t.is_split && !!t.splits?.length);
  const linked = $derived((t.recurring_id ?? 0) > 0);
  const brand = $derived(app.state?.brands?.[t.account_id]);
  // Who chose its category, said as "Set by …".
  const setBy = $derived(sourceLabel(t.category_source));
  const from = $derived(manual ? "Added by you" : [brand?.institution, t.source === "plaid" ? "via Plaid" : t.source === "simplefin" ? "via SimpleFIN" : ""].filter(Boolean).join(" "));
  const top = (c: string | null | undefined) => categories.list.find((x) => x.name === c);
  const ignored = $derived(!!t.category && (t.category === "Ignore" || top(t.category)?.top === "Ignore"));
  const transfer = $derived(!ignored && !!top(t.category)?.is_transfer);

  // The fields follow the transaction (after a save, or a sync) except the one you're in.
  let fName = $state(""), fDate = $state(""), fAmount = $state(""), fNotes = $state("");
  // The direction and category simply follow it (a pick changes them until it's saved, or puts them back).
  let fIn = $derived(t.amount > 0 ? "in" : "out");
  let fCat = $derived(t.category ?? "");
  let nameEl = $state<HTMLInputElement | null>(null), dateEl = $state<HTMLInputElement | null>(null);
  let amountEl = $state<HTMLInputElement | null>(null), notesEl = $state<HTMLTextAreaElement | null>(null);
  const away = (el: Element | null) => !el || document.activeElement !== el;
  $effect(() => { const v = t.payee || t.description || ""; if (away(nameEl)) fName = v; });
  $effect(() => { const v = t.posted.slice(0, 10); if (away(dateEl)) fDate = v; });
  $effect(() => { const v = Math.abs(t.amount).toFixed(2); if (away(amountEl)) fAmount = v; });
  $effect(() => { const v = t.notes ?? ""; if (away(notesEl)) fNotes = v; });

  // What went wrong with each field, said under it.
  let errors = $state<Record<string, string>>({});

  /** Save fields of it (POST /api/transactions/{id}), with an Undo that puts back exactly what was there. */
  async function patch(field: string, body: Record<string, unknown>, message: string): Promise<boolean> {
    errors[field] = "";
    const id = t.id, label = name;
    try {
      const r = await api<{ was: Record<string, unknown>; tx?: Partial<Tx> }>(`/api/transactions/${encodeURIComponent(id)}`, { method: "POST", body });
      if (r.tx) onpatched?.(r.tx);
      undoable(message, async () => {
        await api(`/api/transactions/${encodeURIComponent(id)}`, { method: "POST", body: { restore: r.was } });
        onpatched?.(r.was as Partial<Tx>);
        refreshState(); onchanged();
      }, { description: label || undefined });
      refreshState(); onchanged();
      return true;
    } catch (err) { errors[field] = errMsg(err); return false; }
  }

  function saveName() {
    const v = fName.trim().replace(/\s+/g, " ");
    if (v === (t.payee || t.description || "")) return;
    if (!v && manual) { errors.name = "Give it a name"; return; }
    patch("name", { payee: v }, `${name} → ${v || t.description || "the bank’s name"}`);
  }
  function saveDate() {
    if (fDate === t.posted.slice(0, 10)) return;
    if (!/^\d{4}-\d{2}-\d{2}$/.test(fDate)) { errors.date = "Enter a date"; return; }
    patch("date", { posted: fDate }, `${fmtDate(t.posted)} → ${fmtDate(fDate)}`);
  }
  function saveAmount() {
    const typed = String(fAmount ?? "").trim();   // a number field's value can come back as a number
    const n = Number(typed.replace(/[$,]/g, ""));
    if (typed === "" || !isFinite(n)) { errors.amount = "Enter the amount as a number"; return; }
    const v = Math.round(Math.abs(n) * 100) / 100 * (fIn === "in" ? 1 : -1);
    if (Math.abs(v - t.amount) < 0.005) return;
    patch("amount", { amount: v }, `${fmtSigned(t.amount)} → ${fmtSigned(v)}`)
      .then((ok) => { if (!ok) fIn = t.amount > 0 ? "in" : "out"; });
  }
  function saveNotes() {
    const v = fNotes.trim();
    if (v === (t.notes ?? "")) return;
    patch("notes", { notes: v }, v ? (t.notes ? "Note changed" : "Note added") : "Note removed");
  }
  const enter = (e: KeyboardEvent) => { if (e.key === "Enter") (e.currentTarget as HTMLInputElement).blur(); };

  async function setCategory(v: string) {
    if (!v || v === t.category) return;
    if ((await onsave(t, v)) === false) fCat = t.category ?? "";   // the picker shows the saved one again
  }
  // The two switches set a category (Ignore, Transfer). Turned off, it goes back to the category it had when you
  // turned it on (while the sheet is open), or else to none, for you to pick.
  let before = $state<string | null>(null);
  async function toggle(to: "Ignore" | "Transfer", on: boolean) {
    if (on) {
      if (!ignored && !transfer) before = t.category ?? null;
      return setCategory(to);
    }
    const back = before;
    before = null;
    if (back) return setCategory(back);
    patch("category", { category: null }, `${t.category} → Uncategorized`);
  }

  // A big merchant's name: the brand's a sync gave it, or the bank's text instead (for this one, or for all of the
  // brand's and the syncs from now on), and back. Undo puts back the names and the brand's setting.
  let naming = $state(false);
  async function rename(all: boolean) {
    const b = t.brand;
    if (!b) return;
    naming = false;
    const use = b.using === "brand" ? "bank" : "brand";
    await act(async () => {
      const r = await api<{ updated: number; payee: string; was: Was[]; keep_bank: KeepBank }>(
        `/api/transactions/${encodeURIComponent(t.id)}/name`, { method: "POST", body: { use, all } });
      const message = !all ? `${name} → ${r.payee}` : use === "bank" ? `${b.brand}: the bank’s names from now on` : `${b.brand} from now on`;
      undoable(message, async () => { await restoreTx(r.was, all ? r.keep_bank : undefined); onchanged(); },
        { description: all ? `${r.updated} renamed` : undefined });
      onchanged();
    });
  }

  let splitting = $state(false);
  let picking = $state(false);
  let splitButton = $state<HTMLButtonElement | null>(null);
  let deleting = $state(false);
  async function remove() {
    if (!(await act(async () => {
      await api(`/api/transactions/${encodeURIComponent(t.id)}`, { method: "DELETE" });
      toast.success(`Deleted ${name}`);
      refreshState(); onchanged(); onclose();
    }))) return false;
  }
  const lbl = "flex flex-col gap-1.5 text-xs text-muted-foreground";
</script>

<Sheet.Header>
  <Sheet.Title class="truncate">{name || "Transaction"}</Sheet.Title>
  <Sheet.Description class="flex flex-wrap items-center gap-x-2 gap-y-1">
    <span class={cn("text-2xl font-bold tabular-nums text-foreground", t.amount > 0 && "text-good", t.pending && "opacity-70")}>{fmtSigned(t.amount)}</span>
    {#if t.pending}<Badge variant="secondary">pending</Badge>{/if}
  </Sheet.Description>
</Sheet.Header>

<div class="flex flex-col gap-4 px-4 pb-6">
  <label class={lbl}>Name
    <Input bind:ref={nameEl} bind:value={fName} onchange={saveName} onkeydown={enter} class="text-foreground" aria-invalid={!!errors.name || undefined} />
    {#if errors.name}<span class="text-destructive" role="alert">{errors.name}</span>{/if}
    {#if t.brand}
      <span class="flex flex-wrap gap-x-3">
        {#if naming}
          <Button variant="link" size="sm" class="h-auto p-0 text-xs" onclick={() => rename(false)}>Just this one</Button>
          <Button variant="link" size="sm" class="h-auto p-0 text-xs" onclick={() => rename(true)}>All {t.brand.brand}, from now on</Button>
        {:else}
          <Button variant="link" size="sm" class="h-auto p-0 text-xs" onclick={() => (naming = true)}
            title={`Rename it “${t.brand.using === "brand" ? t.brand.bank_name : t.brand.brand}”`}>
            {t.brand.using === "brand" ? "Use the bank’s name" : `Use “${t.brand.brand}”`}</Button>
        {/if}
      </span>
    {/if}
  </label>

  <div class={lbl}>
    <span id={`cat-${t.id}`}>Category</span>
    {#if split}
      <span class="flex items-center gap-3 text-sm text-foreground">
        <span class="min-w-0 truncate">{(t.splits ?? []).map((s) => `${s.category} ${fmt(Math.abs(s.amount))}`).join(" · ")}</span>
      </span>
    {:else}
      <CategorySelect bind:value={fCat} label="Category" class="h-10 w-full text-foreground" repick={!!t.needs_review} onchange={setCategory} />
      {#if t.category && setBy}<span>Set by {setBy}{t.needs_review && t.category_source === "ai" ? " · to review" : ""}</span>{/if}
    {/if}
  </div>

  <div class="grid grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)] gap-3">
    <label class={lbl}>
      <span>Date{#if t.bank_posted}<span class="ml-1.5 text-warning" title={`Edited · the bank’s: ${fmtDate(t.bank_posted, { month: "short", day: "numeric", year: "numeric" })}`}>edited</span>{/if}</span>
      <Input type="date" bind:ref={dateEl} bind:value={fDate} disabled={locked} onchange={saveDate} class="text-foreground"
        title={locked ? "Pending: the bank sets it when it posts" : undefined} aria-invalid={!!errors.date || undefined} />
      {#if errors.date}<span class="text-destructive" role="alert">{errors.date}</span>{/if}
    </label>
    <div class={lbl}>
      <label for={`amount-${t.id}`}>Amount{#if t.bank_amount != null}<span class="ml-1.5 text-warning" title={`Edited · the bank’s: ${fmtSigned(t.bank_amount)}`}>edited</span>{/if}</label>
      <div class="flex items-center gap-2">
        <Input id={`amount-${t.id}`} type="number" inputmode="decimal" step="0.01" min="0" bind:ref={amountEl} bind:value={fAmount} disabled={locked}
          onchange={saveAmount} onkeydown={enter} class="min-w-0 text-right text-foreground tabular-nums"
          title={locked ? "Pending: the bank sets it when it posts" : undefined} aria-invalid={!!errors.amount || undefined} />
      </div>
      <Segmented label="Money in or out" bind:value={fIn} class={cn("w-full", locked && "pointer-events-none opacity-50")}
        options={[{ value: "out", label: "Out", disabled: locked }, { value: "in", label: "In", disabled: locked }]} onchange={saveAmount} />
      {#if errors.amount}<span class="text-destructive" role="alert">{errors.amount}</span>{/if}
    </div>
  </div>

  <label class={lbl}>Note
    <textarea bind:this={notesEl} bind:value={fNotes} onchange={saveNotes} rows="2" maxlength="1000"
      class="min-h-16 w-full rounded-lg border border-transparent px-3 py-2 text-base text-foreground outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 dark:bg-input md:text-sm"></textarea>
    {#if errors.notes}<span class="text-destructive" role="alert">{errors.notes}</span>{/if}
  </label>

  <div class="flex flex-col border-y py-1">
    <Switch label="Exclude from reports and budgets" checked={ignored} title="Marks it Ignore" onchange={(on) => toggle("Ignore", on)} />
    <Switch label="This is a transfer" checked={transfer} title="Money moving between your own accounts: not spending or income" onchange={(on) => toggle("Transfer", on)} />
    {#if errors.category}<span class="pb-2 text-xs text-destructive" role="alert">{errors.category}</span>{/if}
  </div>

  <div class="flex flex-wrap gap-2">
    <Button bind:ref={splitButton} variant="outline" size="sm" aria-expanded={splitting} onclick={() => (splitting = !splitting)}>{split ? "Edit split" : "Split"}</Button>
    {#if !picking}
      <Button variant="outline" size="sm" class="min-w-0" onclick={() => (picking = true)} title={linked ? "Change the recurring item" : undefined}>
        <span class="truncate">{linked ? `Recurring: ${t.recurring_name}` : "Link to recurring"}</span></Button>
    {:else}
      <RecurringPicker {t} items={recurring} onclose={() => (picking = false)} {onchanged} />
    {/if}
  </div>
  {#if splitting}
    <SplitEditor {t} returnFocus={splitButton} onclose={() => (splitting = false)} onsaved={() => { splitting = false; onchanged(); }} />
  {/if}

  <dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 text-sm">
    <dt class="text-muted-foreground">Account</dt><dd class="min-w-0"><AcctLabel id={t.account_id} name={t.account_name ?? ""} /></dd>
    {#if from}<dt class="text-muted-foreground">Source</dt><dd>{from}</dd>{/if}
    {#if t.description && !manual}<dt class="text-muted-foreground">Bank’s text</dt><dd class="break-words">{t.description}</dd>{/if}
    {#if t.pending}<dt class="text-muted-foreground">Status</dt><dd>Pending</dd>{/if}
  </dl>

  {#if t.retail}
    <OrderDetail orderId={t.retail.order_id} {family} onchange={onchanged} />
  {/if}

  {#if manual}
    <Button variant="ghost" size="sm" class="self-start text-destructive hover:text-destructive" onclick={() => (deleting = true)}>Delete transaction</Button>
    <ConfirmDialog bind:open={deleting} title={`Delete ${name || "this transaction"}?`} destructive confirmLabel="Delete" busyLabel="Deleting…"
      description="It leaves reports, budgets and the list. This can’t be undone." onconfirm={remove} />
  {/if}
</div>
