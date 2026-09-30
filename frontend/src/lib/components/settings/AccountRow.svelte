<script lang="ts" module>
  // Rows you opened stay open when the page redraws after a save.
  const openAccounts = new Set<string>();

  // Why a linked card has no statement yet (Plaid's error code, if it gave one).
  const STATEMENT_NOTES: Record<string, string> = {
    ADDITIONAL_CONSENT_REQUIRED: "Plaid needs your consent to share card statements: reconnect this bank in Settings → Connections.",
    PRODUCTS_NOT_SUPPORTED: "This bank doesn't share card statements through Plaid.", INSTITUTION_NOT_SUPPORTED: "This bank doesn't share card statements through Plaid.",
    INVALID_PRODUCT: "Card statements (Liabilities) aren't enabled for your Plaid account.", PRODUCTS_NOT_ENABLED: "Card statements (Liabilities) aren't enabled for your Plaid account.",
    PRODUCT_NOT_READY: "Plaid is still gathering the statement; it usually arrives with the next sync.",
    NO_LIABILITY_ACCOUNTS: "Plaid didn't find card statements at this bank.",
  };
  const statementNote = (code?: string | null) => STATEMENT_NOTES[code ?? ""] || "It usually arrives with the next sync.";
  const KINDS = ["checking", "savings", "credit", "loan", "investment"];
</script>

<script lang="ts">
  import { api } from "$lib/api";
  import { app, reload } from "$lib/app.svelte";
  import { autosave } from "$lib/autosave";
  import OwnerSelect from "$lib/components/OwnerSelect.svelte";
  import { Badge } from "$lib/components/ui/badge";
  import { fmt, nb } from "$lib/format";
  import { accountName } from "$lib/types";
  import { fromAction } from "svelte/attachments";
  import { toast } from "svelte-sonner";
  import ChevronRight from "@lucide/svelte/icons/chevron-right";
  import BankIcon from "./BankIcon.svelte";
  import type { SettingsAccount } from "./types";
  import { checkCls, fieldCls, inputCls, selectCls, warnText } from "./ui";

  // One account: a compact line (name, what's notable, balance) that opens into its settings, each saved as you go.
  let { a, cash, byName }: { a: SettingsAccount; cash: SettingsAccount[]; byName: Record<string, string> } = $props();

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
  let changingType = $state(false);
  let open = $state(openAccounts.has(init.id));

  const owes = $derived(a.kind === "credit" || a.kind === "loan");
  const bank = $derived(`${a.org && !a.name.toLowerCase().includes(a.org.toLowerCase()) ? a.org + " " : ""}${a.name}`);
  // Owners: first names of the people who have signed in, plus "Joint".
  const owners = $derived(app.state?.owners ?? []);
  const link = $derived(a.plaid_link);
  // Where balances and transactions come from: a choice once the account is matched to a Plaid account.
  const canSwitch = $derived(!a.id.startsWith("pl:") && !!link?.transactions);
  const where = $derived(`${link?.institution || "Plaid"}${link?.mask ? ` ••${link.mask}` : ""}`);

  // The line under the name: "primary · Anthony · paid from Checking · via Plaid", with what needs a look in orange.
  const summary = $derived.by(() => {
    const bits: { text: string; warn?: boolean; tag?: boolean; title?: string }[] = [];
    if (a.id === app.state?.primary_account) bits.push({ text: "primary", tag: true });
    if (a.owner) bits.push({ text: a.owner });
    if (a.kind === "credit") {
      bits.push(a.pay_from ? { text: `paid from ${byName[a.pay_from] || "?"}` } : { text: "no paying account", warn: true });
      if (!link) bits.push({ text: "not linked through Plaid", warn: true });
      else if (!link.closed) bits.push({ text: `no statement from ${link.institution || "the bank"} yet`, warn: true, title: statementNote(link.statement_note) });
    }
    if (a.networth_hidden) bits.push({ text: "not in net worth", title: "Left out of the Net worth page; still counted everywhere else" });
    if (a.provider === "plaid" || a.id.startsWith("pl:")) bits.push({ text: "via Plaid" });
    return bits;
  });

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

  async function setProvider(e: Event) {
    const v = (e.currentTarget as HTMLSelectElement).value;
    try {
      await api(`/api/accounts/${encodeURIComponent(a.id)}`, { method: "POST", body: { provider: v } });
      toast.success(v === "plaid" ? "This account now comes from Plaid; its transactions arrive with the next sync" : "Back to SimpleFIN");
      if (v === "plaid") api("/api/sync", { method: "POST" }).then(() => reload(), () => {});
    } catch (err) { toast.error((err as Error).message); reload(); }
  }
  let kindSelect = $state<HTMLSelectElement | null>(null);
</script>

<details class="group border-b last:border-b-0" {open} ontoggle={toggled}>
  <summary class="flex cursor-pointer list-none items-center gap-3 rounded-lg px-1 py-3 hover:bg-muted/50 [&::-webkit-details-marker]:hidden">
    <BankIcon id={a.id} />
    <span class="flex min-w-0 flex-1 flex-col">
      <span class="truncate font-medium">{name.trim() || a.name}</span>
      <span class="text-xs text-muted-foreground">
        {#each summary as bit, i (i)}{#if i}{" · "}{/if}{#if bit.tag}<Badge variant="secondary">{bit.text}</Badge>{:else}<span
          class={bit.warn ? warnText : ""} title={bit.title}>{nb(bit.text)}</span>{/if}{/each}
      </span>
    </span>
    <span class="shrink-0 tabular-nums">{fmt(a.balance)}</span>
    <ChevronRight class="size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-90" aria-hidden="true" />
  </summary>

  <div class="grid gap-4 pb-4 pl-1 pt-1 sm:grid-cols-2 sm:pl-10 lg:grid-cols-3">
    <label class={`${fieldCls} sm:col-span-2 lg:col-span-3`}>Name
      <input class={`${inputCls} sm:max-w-md`} bind:value={name} placeholder={a.name} use:autosave={() => save(false)} />
      {#if a.display_name}<span class="truncate text-xs" title={bank}>From the bank: {bank}</span>{/if}
    </label>
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
    {#if canSwitch}
      <label class={fieldCls} title="Where balances and transactions come from. Switching keeps your history; transactions both have are matched up.">
        Transactions from
        <select class={selectCls} value={a.provider === "plaid" ? "plaid" : "simplefin"} onchange={setProvider}>
          <option value="simplefin">SimpleFIN</option>
          <option value="plaid">Plaid ({where})</option>
        </select>
      </label>
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
    </div>
  </div>
</details>
