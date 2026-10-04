<script lang="ts" module>
  import { act, errMsg } from "$lib/act";
  // Rows you opened stay open when the page redraws after a save.
  const openAccounts = new Set<string>();

  // Why a linked card has no statement from Plaid (Plaid's error code, if it gave one); you can enter it yourself meanwhile.
  const STATEMENT_NOTES: Record<string, string> = {
    ADDITIONAL_CONSENT_REQUIRED: "Plaid needs your consent to share card statements: reconnect this bank in Settings → Connections.",
    PRODUCTS_NOT_SUPPORTED: "This bank doesn't share card statements through Plaid.", INSTITUTION_NOT_SUPPORTED: "This bank doesn't share card statements through Plaid.",
    INVALID_PRODUCT: "Card statements (Liabilities) aren't enabled for your Plaid account.", PRODUCTS_NOT_ENABLED: "Card statements (Liabilities) aren't enabled for your Plaid account.",
    PRODUCT_NOT_READY: "Plaid is still gathering the statement; it usually arrives with the next sync.",
    NO_LIABILITY_ACCOUNTS: "Plaid didn't find card statements at this bank.",
  };
  const statementNote = (code?: string | null) => STATEMENT_NOTES[code ?? ""] || "Plaid hasn’t sent a statement yet; it usually arrives with the next sync.";
</script>

<script lang="ts">
  import { api } from "$lib/api";
  import { ACCOUNT_KINDS, isCash } from "$lib/accounts";
  import { app, refreshState, reload } from "$lib/app.svelte";
  import { autosave } from "$lib/autosave";
  import OwnerSelect from "$lib/components/OwnerSelect.svelte";
  import { Badge } from "$lib/components/ui/badge";
  import { Button } from "$lib/components/ui/button";
  import { ConfirmDialog } from "$lib/components/ui/confirm-dialog";
  import { fmt, fmtDate, plural } from "$lib/format";
  import { undoable } from "$lib/undo";
  import { fromAction } from "svelte/attachments";
  import { tick } from "svelte";
  import { toast } from "svelte-sonner";
  import ChevronRight from "@lucide/svelte/icons/chevron-right";
  import LogoPicker from "$lib/components/transactions/LogoPicker.svelte";
  import BankIcon from "./BankIcon.svelte";
  import CardStatement from "./CardStatement.svelte";
  import { accountFocus } from "./accountFocus.svelte";
  import { sourceInfo } from "./plaidAccounts";
  import CardTerms from "./CardTerms.svelte";
  import LoanTerms from "./LoanTerms.svelte";
  import PlaidLink from "./PlaidLink.svelte";
  import type { AccountRemoval, PlaidStatus, SettingsAccount } from "./types";
  import { checkCls, fieldCls, inputCls, linkCls, selectCls, warnText } from "./ui";

  // One account: a compact line (name, where it syncs from, what's notable, balance) that opens into its settings, each
  // saved as you go, under a row of actions (use it for the forecast, rename, hide). Hiding one tells the list
  // (`onhidden`), which moves it to the hidden ones without loading the page again.
  let { a, cash, byName, plaid = null, mine = [], onhidden }: {
    a: SettingsAccount; cash: SettingsAccount[]; byName: Record<string, string>; plaid?: PlaidStatus | null; mine?: SettingsAccount[];
    onhidden?: (id: string, hidden: boolean) => void;
  } = $props();

  // svelte-ignore state_referenced_locally
  const init = a;
  let name = $state(init.display_name || "");
  let owner = $state(init.owner || "");
  let payFrom = $state(init.pay_from || "");
  // How the card's statements are paid, for the forecast: in full, the minimum, or a fixed amount (and its APR, for the
  // interest on what that leaves to carry over).
  let payMode = $state(init.pay_mode || "full");
  let payAmount = $state<number | null>(init.pay_amount ?? null);
  let apr = $state<number | null>(init.apr ?? null);
  let kind = $state(init.kind);
  let hidden = $state(!!init.hidden);
  let counted = $state(!init.networth_hidden);
  let sign = $state(!!init.owed_positive);
  let changingType = $state(false);
  let open = $state(openAccounts.has(init.id));

  const owes = $derived(a.kind === "credit" || a.kind === "loan");
  // A loan's terms, for the retirement planner: the lender's through Plaid when it shares them, else yours.
  const loan = $derived(a.kind === "loan" ? a.loan : undefined);
  const bank = $derived(`${a.org && !a.name.toLowerCase().includes(a.org.toLowerCase()) ? a.org + " " : ""}${a.name}`);
  // Owners: first names of the people who have signed in, plus "Joint".
  const owners = $derived(app.state?.owners ?? []);
  // With one person there is nobody to choose between; an owner already set to someone else still shows.
  const showOwner = $derived(owners.length > 1 || (!!owner && !owners.includes(owner)));
  // Where its balances and transactions come from (the row's summary says so; the Data source section is PlaidLink).
  const info = $derived(sourceInfo(a, plaid));
  const link = $derived(info.link);
  const own = $derived(info.own);
  const source = $derived(info.source);

  // The line under the name: "Forecast · Alex · paid from Checking · via Plaid", with what needs a look in orange.
  // "Forecast" marks the forecast account; another checking or savings account offers "Use for the forecast" among its
  // actions. With no choice made, a lone checking account is the one the forecast uses.
  const forecast = $derived(a.id === app.state?.primary_account
    || (!app.state?.primary_account && a.kind === "checking" && cash.filter((c) => c.kind === "checking" && !c.hidden).length === 1));
  const canForecast = $derived(isCash(a.kind) && !hidden && !forecast);
  const label = $derived(name.trim() || a.name);
  const summary = $derived.by(() => {
    const bits: { text: string; warn?: boolean; tag?: boolean; title?: string; link?: boolean }[] = [];
    if (forecast) bits.push({ text: "Forecast", tag: true, title: "The forecast account: Overview forecasts its balance" });
    if (a.owner) bits.push({ text: a.owner });
    if (a.kind === "credit") {
      bits.push(a.pay_from ? { text: `paid from ${byName[a.pay_from] || "?"}` } : { text: "no paying account", warn: true });
      if (a.pay_mode === "minimum") bits.push({ text: "pays the minimum" });
      else if (a.pay_mode === "fixed")
        bits.push(a.pay_amount != null ? { text: `pays ${fmt(a.pay_amount)} a statement` } : { text: "no fixed amount", warn: true, title: "Paid in full until you enter one" });
      // Its statement: Plaid's says nothing more; one you entered says when it's due; none (or an old one) asks for it.
      const st = a.statement;
      if (!st) bits.push({ text: "no statement", warn: true, title: link ? statementNote(link.statement_note) : undefined }, { text: "Enter…", link: true });
      else if (st.source === "manual" && st.stale) bits.push({ text: "statement out of date", warn: true }, { text: "Enter…", link: true });
      else if (st.source === "manual" && st.due) bits.push({ text: `statement due ${fmtDate(st.due)}` });
    }
    if (a.networth_hidden) bits.push({ text: "not in net worth", title: "Left out of the Net worth page; still counted everywhere else" });
    bits.push({ text: source, title: a.provider === "plaid" || own ? "Balances and transactions come from Plaid" : "Balances and transactions come from SimpleFIN" });
    return bits;
  });

  // The same setting as the account picker in Overview's forecast settings.
  async function useForForecast() {
    await act(async () => {
      await api("/api/settings", { method: "POST", body: { primary_account: a.id } });
      toast.success(`${label} is now the forecast account`);
      await refreshState();
    });
  }

  function rename() {
    const box = document.getElementById(`name-${a.id}`) as HTMLInputElement | null;
    box?.focus(); box?.select();
  }

  // Hiding (or showing again) saves just that, then the list moves the row without redrawing the page; hiding offers Undo.
  const setHiddenOnServer = (on: boolean) => api(`/api/accounts/${encodeURIComponent(a.id)}`, { method: "POST", body: { hidden: on ? 1 : 0 } });
  async function setHidden(on: boolean) {
    if (!(await act(async () => { await setHiddenOnServer(on); }))) return;
    hidden = on;
    openAccounts.delete(a.id);
    const id = a.id, what = label, moved = onhidden;
    // The hide itself is saved and the row has moved; refreshing the app's state after it only updates counts elsewhere, and
    // the next state check does that if this one fails, so it stays quiet.
    const done = (v: boolean) => { if (moved) { moved(id, v); Promise.resolve().then(() => refreshState()).catch(() => { /* stays quiet: see above */ }); } else reload(); };
    done(on);
    if (on) undoable(`${what} hidden`, async () => { await setHiddenOnServer(false); done(false); });
    else toast.success(`${what} is shown again`);
  }

  function toggled(e: Event) {
    open = (e.currentTarget as HTMLDetailsElement).open;
    if (open) openAccounts.add(a.id); else openAccounts.delete(a.id);
  }

  // Saves the whole row. Fields that change the row's summary (owner, paying account, how it's paid, hidden, net worth, type) redraw the page.
  async function save(rerender: boolean) {
    const body: Record<string, unknown> = { display_name: name, kind, hidden: hidden ? 1 : 0, networth_hidden: counted ? 0 : 1, owner };
    if (a.kind === "credit") Object.assign(body, { pay_from: payFrom, pay_mode: payMode, pay_amount: payAmount ?? "", apr: apr ?? "" });
    if (owes) body.owed_positive = sign ? 1 : 0;
    try {
      await api(`/api/accounts/${encodeURIComponent(a.id)}`, { method: "POST", body });
      if (rerender) { toast.success("Saved"); reload(); }
    } catch (err) { if (rerender) toast.error(errMsg(err)); else throw err; }
  }

  let kindSelect = $state<HTMLSelectElement | null>(null);

  // "Enter…" on the line, or a link to #setup/accounts?account=<id> (Overview's "Enter Visa's latest statement"), opens
  // the row at its Statement section.
  let statementBox = $state<HTMLElement | null>(null);
  async function openStatement(e?: Event) {
    e?.preventDefault(); e?.stopPropagation();
    open = true; openAccounts.add(a.id);
    await tick();
    statementBox?.querySelector<HTMLElement>("[data-enter-next]")?.click();   // one entered already: "Enter the next statement"
    await tick();
    statementBox?.scrollIntoView({ block: "nearest" });
    statementBox?.querySelector<HTMLElement>("form input")?.focus();
  }
  $effect(() => {
    if (accountFocus.id !== a.id) return;
    accountFocus.id = "";
    if (a.kind === "credit") openStatement();
    else { open = true; openAccounts.add(a.id); }
  });

  // Deleting the account: the dialog says what goes with it (asked when it opens). One with more transactions than this
  // asks for its name to be typed.
  const TYPE_ABOVE = 50;
  let removing = $state(false);
  let removal = $state<AccountRemoval | null>(null);
  async function askRemove() {
    removal = null;
    removing = true;
    try { removal = await api<AccountRemoval>(`/api/accounts/${encodeURIComponent(a.id)}/removal`); }
    catch (err) { toast.error(errMsg(err)); removing = false; }
  }
  async function remove() {
    if (!(await act(async () => {
      await api(`/api/accounts/${encodeURIComponent(a.id)}/remove`, { method: "POST" });
      toast.success(`${label} deleted`);
      openAccounts.delete(a.id);
      reload();
    }))) return false;
  }
</script>

<details class="group border-b last:border-b-0" {open} ontoggle={toggled}>
  <summary class="flex cursor-pointer list-none items-center gap-3 rounded-lg px-1 py-3 hover:bg-muted/50 [&::-webkit-details-marker]:hidden">
    <BankIcon id={a.id} />
    <!-- On a phone the name may take two lines and the line under it wraps between words; the balance stays on one. -->
    <span class="flex min-w-0 flex-1 flex-col">
      <span class="line-clamp-2 font-medium break-words">{label}</span>
      <span class="text-xs text-muted-foreground [overflow-wrap:break-word]">
        {#each summary as bit, i (i)}{#if i}{" · "}{/if}{#if bit.tag}<Badge variant="secondary" title={bit.title}>{bit.text}</Badge>{:else if bit.link}<button
          type="button" class="font-medium text-foreground underline underline-offset-4" onclick={openStatement}>{bit.text}</button>{:else}<span
          class={bit.warn ? warnText : ""} title={bit.title}>{bit.text}</span>{/if}{/each}
      </span>
    </span>
    <span class="shrink-0 whitespace-nowrap tabular-nums">{fmt(a.balance)}</span>
    <ChevronRight class="size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-90" aria-hidden="true" />
  </summary>

  <div class="grid gap-4 pb-4 pl-1 pt-1 sm:grid-cols-2 sm:pl-10 lg:grid-cols-3">
    <div class="flex flex-wrap gap-2 sm:col-span-2 lg:col-span-3" role="group" aria-label="Account actions">
      {#if canForecast}<Button size="sm" onclick={useForForecast}>Use for the forecast</Button>{/if}
      <Button variant="outline" size="sm" onclick={rename}>Rename</Button>
      <Button variant="outline" size="sm" onclick={() => setHidden(!hidden)}>{hidden ? "Show again" : "Hide"}</Button>
    </div>
    <div class={`${fieldCls} sm:col-span-2 lg:col-span-3`}>
      <label for={`name-${a.id}`}>Name</label>
      <span class="flex items-center gap-2 sm:max-w-md">
        <!-- The logo, which opens the picker as a payee's does in Transactions: the institution's, or one you chose (a
             website's, or its letter), wherever the account shows. -->
        <LogoPicker account={a.id} name={name.trim() || a.name} onchanged={() => refreshState()}>
          <span class="flex size-9 items-center justify-center rounded-lg transition-colors hover:bg-muted"><BankIcon id={a.id} /></span>
        </LogoPicker>
        <input id={`name-${a.id}`} class={`${inputCls} min-w-0 flex-1`} bind:value={name} placeholder={a.name} use:autosave={() => save(false)} />
      </span>
      {#if a.display_name}<span class="truncate text-xs" title={bank}>From the bank: {bank}</span>{/if}
    </div>
    {#if showOwner}
      <label class={fieldCls}>Owner
        <OwnerSelect bind:value={owner} {owners} joint blank="—" {@attach fromAction(autosave, () => () => save(true))} />
      </label>
    {/if}
    {#if a.kind === "credit"}
      <CardTerms {a} {cash} bind:payFrom bind:payMode bind:payAmount bind:apr save={save} />
    {/if}
    {#if loan}
      <LoanTerms {a} {loan} />
    {/if}
    {#if a.kind === "credit"}
      <section class="flex flex-col gap-3 rounded-lg border p-3 sm:col-span-2 lg:col-span-3" aria-label="Statement" bind:this={statementBox}>
        <CardStatement {a} note={link && !a.statement ? statementNote(link.statement_note) : ""} />
      </section>
    {/if}
    <PlaidLink {a} {plaid} {mine} {info} />
    <section class="flex flex-col gap-2.5 rounded-lg border p-3 sm:col-span-2 lg:col-span-3" aria-label="Options">
      <h4 class="text-sm font-medium">Options</h4>
      {#if owes}
        <label class={checkCls}><input type="checkbox" bind:checked={sign} use:autosave={() => save(false)} /> Bank reports what's owed as a positive number</label>
      {/if}
      <label class={checkCls} title="Off leaves this account out of the Net worth page; it still shows everywhere else">
        <input type="checkbox" bind:checked={counted} use:autosave={() => save(true)} /> Count in net worth</label>
    </section>
    <div class="flex flex-wrap items-center gap-x-4 gap-y-2 sm:col-span-2 lg:col-span-3">
      <span class="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
        {a.kind} account ·
        {#if changingType}
          <select class={`${selectCls} w-36`} aria-label="Account type" bind:this={kindSelect} bind:value={kind} onchange={() => save(true)}>
            {#each ACCOUNT_KINDS as k (k)}<option>{k}</option>{/each}
          </select>
        {:else}
          <button type="button" class="font-medium text-foreground underline-offset-4 hover:underline"
            onclick={async () => { changingType = true; await Promise.resolve(); kindSelect?.focus(); }}>change type</button>
        {/if}
      </span>
      <span class="text-sm">
        <button type="button" class="font-medium text-destructive underline-offset-4 hover:underline" onclick={askRemove}>Delete account…</button>
      </span>
    </div>
  </div>
</details>

<!-- Waits for the count of what goes with it; with a long history, for the account's name typed too. -->
<ConfirmDialog bind:open={removing} title={`Delete ${label}?`} confirmLabel="Delete" busyLabel="Deleting…" destructive onconfirm={remove}
  disabled={!removal} typeToConfirm={removal && removal.transactions > TYPE_ABOVE ? label : undefined}>
  {#snippet description()}
    <p>Deletes the account and everything Runway keeps for it:</p>
    {#if removal}
      <ul class="list-disc space-y-1 pl-5">
        <li>{removal.transactions ? `${plural(removal.transactions, "transaction")}, with their categories and splits` : "No transactions"}</li>
        {#if removal.recurring}<li>{plural(removal.recurring, "recurring item")} on this account</li>{/if}
        {#if removal.rules}<li>{plural(removal.rules, "rule")} that only {removal.rules === 1 ? "applies" : "apply"} to it</li>{/if}
        {#if removal.statements}<li>{plural(removal.statements, "statement")} you entered</li>{/if}
        {#if removal.holdings}<li>{plural(removal.holdings, "holding")}, with the account’s investment history</li>{/if}
        <li>Budgets, cards and loans that point at it let go of it.</li>
      </ul>
    {:else}<p>Counting what goes with it…</p>{/if}
    <p>{removal?.plaid ? "SimpleFIN and Plaid leave" : "Syncs leave"} it out until you restore it from the bottom of this list, which brings back the account but not what was deleted with it.</p>
    <p><a class={linkCls} href="#setup/advanced" onclick={() => (removing = false)}>Download a backup first</a></p>
  {/snippet}
</ConfirmDialog>
