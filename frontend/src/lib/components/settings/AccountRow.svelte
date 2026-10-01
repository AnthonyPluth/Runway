<script lang="ts" module>
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
  const KINDS = ["checking", "savings", "credit", "loan", "investment"];
</script>

<script lang="ts">
  import { api } from "$lib/api";
  import { app, refreshState, reload } from "$lib/app.svelte";
  import { autosave } from "$lib/autosave";
  import OwnerSelect from "$lib/components/OwnerSelect.svelte";
  import { Badge } from "$lib/components/ui/badge";
  import { Button } from "$lib/components/ui/button";
  import { ConfirmDialog } from "$lib/components/ui/confirm-dialog";
  import { fmt, fmtDate, fmtDateTime, nb, plural } from "$lib/format";
  import { accountName } from "$lib/types";
  import { fromAction } from "svelte/attachments";
  import { tick } from "svelte";
  import { toast } from "svelte-sonner";
  import ChevronRight from "@lucide/svelte/icons/chevron-right";
  import LogoPicker from "$lib/components/transactions/LogoPicker.svelte";
  import BankIcon from "./BankIcon.svelte";
  import CardStatement from "./CardStatement.svelte";
  import { accountFocus } from "./accountFocus.svelte";
  import { linkable, linkableInvestments, plaidFor, plaidLabel } from "./plaidAccounts";
  import { connectPlaid, matchPlaidAccount } from "./plaid.svelte";
  import PlaidChoice from "./PlaidChoice.svelte";
  import type { AccountRemoval, PlaidStatus, SettingsAccount } from "./types";
  import { checkCls, fieldCls, inputCls, linkCls, rowCls, selectCls, warnText } from "./ui";

  // One account: a compact line (name, where it syncs from, what's notable, balance) that opens into its settings, each saved as you go.
  let { a, cash, byName, plaid = null, mine = [] }: {
    a: SettingsAccount; cash: SettingsAccount[]; byName: Record<string, string>; plaid?: PlaidStatus | null; mine?: SettingsAccount[];
  } = $props();

  // svelte-ignore state_referenced_locally
  const init = a;
  let name = $state(init.display_name || "");
  let owner = $state(init.owner || "");
  let payFrom = $state(init.pay_from || "");
  let kind = $state(init.kind);
  let hidden = $state(!!init.hidden);
  let counted = $state(!init.networth_hidden);
  let sign = $state(!!init.owed_positive);
  let spend = $state(!!init.daily_spend);
  let rate = $state(init.loan?.set_rate != null ? String(init.loan.set_rate) : "");
  let payment = $state(init.loan?.set_payment != null ? String(init.loan.set_payment) : "");
  let changingType = $state(false);
  let open = $state(openAccounts.has(init.id));

  const owes = $derived(a.kind === "credit" || a.kind === "loan");
  // A loan's terms, for the retirement planner: the lender's through Plaid when it shares them, else yours.
  const loan = $derived(a.kind === "loan" ? a.loan : undefined);
  // What an empty payment means: the one that pays the loan off by Plaid's payoff date, else what recent payments suggest.
  const byPayoff = $derived(!!loan && loan.source === "plaid" && !loan.plaid_payment && loan.payment != null && loan.set_payment == null);
  const dollars = (n: number) => Math.round(n).toLocaleString("en-US");
  const hint = $derived(byPayoff ? `${dollars(loan!.payment!)} to pay it off by ${fmtDate(loan!.maturity!, { month: "short", day: "numeric", year: "numeric" })}`
    : loan?.inferred_payment ? `${dollars(loan.inferred_payment)} from recent payments` : "e.g. 1,850");
  const hintTitle = $derived(byPayoff ? "Left empty, it’s the payment that pays the loan off by the date the lender gives, through Plaid"
    : "Left empty, it’s worked out from the payments into this account lately");
  const bank = $derived(`${a.org && !a.name.toLowerCase().includes(a.org.toLowerCase()) ? a.org + " " : ""}${a.name}`);
  // Owners: first names of the people who have signed in, plus "Joint".
  const owners = $derived(app.state?.owners ?? []);
  const link = $derived(a.plaid_link);
  // Where balances and transactions come from: a choice once the account is matched to a Plaid account.
  const canSwitch = $derived(!a.id.startsWith("pl:") && !!link?.transactions);
  const where = $derived(`${link?.institution || "Plaid"}${link?.mask ? ` ••${link.mask}` : ""}`);
  const own = $derived(a.id.startsWith("pl:"));
  // The Plaid account behind this one (its connection says when it last synced), and the ones it could be linked to.
  const behind = $derived(plaidFor(plaid, a.id));
  const options = $derived(linkable(plaid));
  const linkKind = $derived(["checking", "savings", "credit", "loan"].includes(a.kind));
  // An investment account has no plaid_link: a Plaid account is tied to it by that account's own choice (among its connection's candidates).
  const invKind = $derived(a.kind === "investment");
  const invOptions = $derived(linkableInvestments(plaid, a.id));
  const invLinked = $derived(invKind && !own ? behind : undefined);
  // Shown once Plaid is set up, or this account already uses it.
  const showSource = $derived(!!link || own || !!invLinked || ((linkKind || invKind) && !!plaid && (plaid.configured || plaid.items.length > 0)));
  const mask = $derived(link?.mask ? ` ••${link.mask}` : behind?.p.mask && (own || invLinked) ? ` ••${behind.p.mask}` : "");
  const source = $derived(own ? `Plaid${mask}` : link || invLinked ? `SimpleFIN + Plaid${mask}` : "SimpleFIN");
  const plaidSynced = $derived.by(() => {
    const t = behind?.it.last_sync;
    if (!t) return "";
    const d = new Date(t.replace(" ", "T") + "Z");
    return isNaN(d.getTime()) ? t : fmtDateTime(d);
  });

  // The line under the name: "primary · Anthony · paid from Checking · via Plaid", with what needs a look in orange.
  // Another checking or savings account offers "Use for the forecast" there instead of "primary". With no choice made,
  // a lone checking account is the one the forecast uses.
  const primary = $derived(a.id === app.state?.primary_account
    || (!app.state?.primary_account && a.kind === "checking" && cash.filter((c) => c.kind === "checking" && !c.hidden).length === 1));
  const summary = $derived.by(() => {
    const bits: { text: string; warn?: boolean; tag?: boolean; title?: string; link?: boolean; primary?: boolean }[] = [];
    if (primary) bits.push({ text: "primary", tag: true });
    if (a.owner) bits.push({ text: a.owner });
    if (a.kind === "credit") {
      bits.push(a.pay_from ? { text: `paid from ${byName[a.pay_from] || "?"}` } : { text: "no paying account", warn: true });
      // Its statement: Plaid's says nothing more; one you entered says when it's due; none (or an old one) asks for it.
      const st = a.statement;
      if (!st) bits.push({ text: "no statement", warn: true, title: link ? statementNote(link.statement_note) : undefined }, { text: "Enter…", link: true });
      else if (st.source === "manual" && st.stale) bits.push({ text: "statement out of date", warn: true }, { text: "Enter…", link: true });
      else if (st.source === "manual" && st.due) bits.push({ text: `statement due ${fmtDate(st.due)}` });
    }
    if (a.networth_hidden) bits.push({ text: "not in net worth", title: "Left out of the Net worth page; still counted everywhere else" });
    bits.push({ text: source, title: a.provider === "plaid" || own ? "Balances and transactions come from Plaid" : "Balances and transactions come from SimpleFIN" });
    if ((a.kind === "checking" || a.kind === "savings") && !a.hidden && !primary)
      bits.push({ text: "Use for the forecast", primary: true });
    return bits;
  });

  // The same setting as the account picker in Overview's forecast settings.
  async function makePrimary(e: Event) {
    e.preventDefault(); e.stopPropagation();
    try {
      await api("/api/settings", { method: "POST", body: { primary_account: a.id } });
      toast.success(`Overview now forecasts ${name.trim() || a.name}`);
      await refreshState();
    } catch (err) { toast.error((err as Error).message); }
  }

  function toggled(e: Event) {
    open = (e.currentTarget as HTMLDetailsElement).open;
    if (open) openAccounts.add(a.id); else openAccounts.delete(a.id);
  }

  // Saves the whole row. Fields that change the row's summary (owner, paying account, hidden, net worth, type) redraw the page.
  async function save(rerender: boolean) {
    const body: Record<string, unknown> = { display_name: name, kind, hidden: hidden ? 1 : 0, networth_hidden: counted ? 0 : 1, owner };
    if (a.kind === "credit") body.pay_from = payFrom;
    if (owes) body.owed_positive = sign ? 1 : 0;
    if (a.kind === "checking" || a.kind === "savings") body.daily_spend = spend ? 1 : 0;
    try {
      await api(`/api/accounts/${encodeURIComponent(a.id)}`, { method: "POST", body });
      if (rerender) { toast.success("Saved"); reload(); }
    } catch (err) { if (rerender) toast.error((err as Error).message); else throw err; }
  }

  // A loan's interest rate and monthly payment, the ones Plaid doesn't supply (an empty payment is worked out from
  // recent payments).
  async function saveLoan() {
    const body: Record<string, unknown> = {};
    if (!loan?.plaid) body.interest_rate = rate;
    if (!loan?.plaid_payment) body.monthly_payment = payment;
    await api(`/api/accounts/${encodeURIComponent(a.id)}`, { method: "POST", body });
  }

  async function setProvider(e: Event) {
    const v = (e.currentTarget as HTMLSelectElement).value;
    try {
      await api(`/api/accounts/${encodeURIComponent(a.id)}`, { method: "POST", body: { provider: v } });
      toast.success(v === "plaid" ? "This account now comes from Plaid; its transactions arrive with the next sync" : "Back to SimpleFIN");
      if (v === "plaid") api("/api/sync", { method: "POST" }).then(() => reload(), () => {});
    } catch (err) { toast.error((err as Error).message); reload(); }
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

  // Deleting the account: the dialog says what goes with it (asked when it opens).
  let removing = $state(false);
  let removal = $state<AccountRemoval | null>(null);
  async function askRemove() {
    removal = null;
    removing = true;
    try { removal = await api<AccountRemoval>(`/api/accounts/${encodeURIComponent(a.id)}/removal`); }
    catch (err) { toast.error((err as Error).message); removing = false; }
  }
  async function remove() {
    try {
      await api(`/api/accounts/${encodeURIComponent(a.id)}/remove`, { method: "POST" });
      toast.success(`${name.trim() || a.name} deleted`);
      openAccounts.delete(a.id);
      reload();
    } catch (err) { toast.error((err as Error).message); return false; }
  }
  let linking = $state("");
  async function linkTo(e: Event) {
    const el = e.currentTarget as HTMLSelectElement;
    const v = el.value;
    if (!v) return;
    linking = v;
    if (v === "__connect") await connectPlaid(invKind ? "investments" : "bank");
    else await matchPlaidAccount(v, a.id, !invKind);
    linking = ""; el.value = "";
  }
</script>

<details class="group border-b last:border-b-0" {open} ontoggle={toggled}>
  <summary class="flex cursor-pointer list-none items-center gap-3 rounded-lg px-1 py-3 hover:bg-muted/50 [&::-webkit-details-marker]:hidden">
    <BankIcon id={a.id} />
    <span class="flex min-w-0 flex-1 flex-col">
      <span class="truncate font-medium">{name.trim() || a.name}</span>
      <span class="text-xs text-muted-foreground">
        {#each summary as bit, i (i)}{#if i}{" · "}{/if}{#if bit.tag}<Badge variant="secondary">{bit.text}</Badge>{:else if bit.link}<button
          type="button" class="font-medium text-foreground underline underline-offset-4" onclick={openStatement}>{bit.text}</button>{:else if bit.primary}<button
          type="button" class="font-medium text-primary" onclick={makePrimary}>{bit.text}</button>{:else}<span
          class={bit.warn ? warnText : ""} title={bit.title}>{nb(bit.text)}</span>{/if}{/each}
      </span>
    </span>
    <span class="shrink-0 tabular-nums">{fmt(a.balance)}</span>
    <ChevronRight class="size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-90" aria-hidden="true" />
  </summary>

  <div class="grid gap-4 pb-4 pl-1 pt-1 sm:grid-cols-2 sm:pl-10 lg:grid-cols-3">
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
    <label class={fieldCls}>Owner
      <OwnerSelect bind:value={owner} {owners} joint blank="—" {@attach fromAction(autosave, () => () => save(true))} />
    </label>
    {#if a.kind === "credit"}
      <label class={fieldCls}>Paid from
        <select class={selectCls} bind:value={payFrom} use:autosave={() => save(true)}>
          <option value="">—</option>
          {#each cash as c (c.id)}<option value={c.id}>{accountName(c)}</option>{/each}
        </select>
      </label>
    {/if}
    {#if loan}
      <!-- Each figure Plaid supplies is the lender's and shown as is; what it leaves out (a new loan's payment, say) can be set. -->
      {#if loan.plaid}
        <div class={fieldCls}>Interest rate
          <span class="flex h-9 items-center gap-2 text-foreground">{+(loan.rate ?? 0).toFixed(3)}%<span class="text-xs text-muted-foreground">from Plaid</span></span>
        </div>
      {:else}
        <label class={fieldCls} title="The loan’s annual interest rate. The retirement planner uses it to work out what’s still owed when you sell.">Interest rate
          <span class="relative">
            <input class={`${inputCls} w-full pr-7`} inputmode="decimal" bind:value={rate} placeholder="e.g. 6.25" use:autosave={saveLoan} />
            <span class="pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 text-xs" aria-hidden="true">%</span>
          </span>
        </label>
      {/if}
      {#if loan.plaid_payment}
        <div class={fieldCls}>Monthly payment
          <span class="flex h-9 items-center gap-2 text-foreground">{fmt(loan.payment)}<span class="text-xs text-muted-foreground">from Plaid</span></span>
        </div>
      {:else}
        <label class={fieldCls} title={hintTitle}>Monthly payment
          <span class="relative">
            <span class="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-xs" aria-hidden="true">$</span>
            <input class={`${inputCls} w-full pl-6`} inputmode="decimal" bind:value={payment} placeholder={hint} use:autosave={saveLoan} />
          </span>
        </label>
      {/if}
    {/if}
    {#if a.kind === "credit"}
      <section class="flex flex-col gap-3 rounded-lg border p-3 sm:col-span-2 lg:col-span-3" aria-label="Statement" bind:this={statementBox}>
        <CardStatement {a} note={link && !a.statement ? statementNote(link.statement_note) : ""} />
      </section>
    {/if}
    {#if showSource}
      <section class="flex flex-col gap-3 rounded-lg border p-3 sm:col-span-2 lg:col-span-3" aria-label="Data source">
        <h4 class="text-sm font-medium">Data source</h4>
        <p class="text-sm text-muted-foreground">{nb(source)}{#if plaidSynced}{" · "}{nb(`Plaid synced ${plaidSynced}`)}{/if}</p>
        <div class={rowCls}>
          {#if canSwitch}
            <label class={fieldCls} title="Where balances and transactions come from. Switching keeps your history; transactions both have are matched up.">
              Transactions from
              <select class={selectCls} value={a.provider === "plaid" ? "plaid" : "simplefin"} onchange={setProvider}>
                <option value="simplefin">SimpleFIN</option>
                <option value="plaid">Plaid ({where})</option>
              </select>
            </label>
          {/if}
          {#if own && behind}
            <label class={fieldCls}>Which of your accounts this is
              <PlaidChoice p={behind.p} it={behind.it} {mine} />
            </label>
          {:else if invLinked}
            <span class="flex items-center gap-2 text-sm">Linked to {plaidLabel(invLinked)}
              <Button variant="outline" size="sm" onclick={() => matchPlaidAccount(invLinked.p.id, "", false)}>Unlink</Button></span>
          {:else if link && a.plaid_account_id}
            <span class="flex items-center gap-2 text-sm">Linked to {where}
              <Button variant="outline" size="sm" onclick={() => matchPlaidAccount(a.plaid_account_id!, "")}>Unlink</Button></span>
          {:else if invKind}
            <label class={fieldCls}>Link to a Plaid account
              <select class={`${selectCls} w-full sm:w-72`} disabled={!!linking} onchange={linkTo}>
                <option value="">Choose…</option>
                {#each invOptions as o (o.p.id)}<option value={o.p.id}>{plaidLabel(o)} · {fmt(o.p.balance)}</option>{/each}
                {#if plaid?.configured}<option value="__connect">Connect an investment account through Plaid…</option>{/if}
              </select>
            </label>
          {:else if !link && linkKind}
            <label class={fieldCls}>Link to a Plaid account
              <select class={`${selectCls} w-full sm:w-72`} disabled={!!linking} onchange={linkTo}>
                <option value="">Choose…</option>
                {#each options as o (o.p.id)}<option value={o.p.id}>{plaidLabel(o)} · {fmt(o.p.balance)}</option>{/each}
                {#if plaid?.configured}<option value="__connect">Connect a new bank through Plaid…</option>{/if}
              </select>
            </label>
          {/if}
        </div>
      </section>
    {/if}
    <div class="flex flex-col gap-2.5 sm:col-span-2 lg:col-span-3">
      {#if a.kind === "checking" || a.kind === "savings"}
        <label class={checkCls} title="Spreads this account's recent non-recurring spending evenly over every day of the forecast">
          <input type="checkbox" bind:checked={spend} use:autosave={() => save(false)} /> Subtract average everyday spending</label>
      {/if}
      {#if owes}
        <label class={checkCls}><input type="checkbox" bind:checked={sign} use:autosave={() => save(false)} /> Bank reports what's owed as a positive number</label>
      {/if}
      <label class={checkCls} title="Off leaves this account out of the Net worth page; it still shows everywhere else">
        <input type="checkbox" bind:checked={counted} use:autosave={() => save(true)} /> Count in net worth</label>
      <label class={checkCls}><input type="checkbox" bind:checked={hidden} use:autosave={() => save(true)} /> Hide this account</label>
      <span class="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
        {a.kind} account ·
        {#if changingType}
          <select class={`${selectCls} w-36`} aria-label="Account type" bind:this={kindSelect} bind:value={kind} onchange={() => save(true)}>
            {#each KINDS as k (k)}<option>{k}</option>{/each}
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

<ConfirmDialog bind:open={removing} title={`Delete ${name.trim() || a.name}?`} confirmLabel="Delete" busyLabel="Deleting…" destructive onconfirm={remove}>
  {#snippet description()}
    <p>Deletes the account and everything Runway keeps for it. This can’t be undone; only a backup has it.</p>
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
    <p>It stays deleted: {removal?.plaid ? "SimpleFIN and Plaid leave" : "syncs leave"} it out until you restore it, at the bottom of Settings → Accounts.</p>
    <p><a class={linkCls} href="#setup/advanced" onclick={() => (removing = false)}>Download a backup first</a></p>
  {/snippet}
</ConfirmDialog>
