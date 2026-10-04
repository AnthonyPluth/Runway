<script lang="ts">
  import { autosave } from "$lib/autosave";
  import { api } from "$lib/api";
  import { commas } from "$lib/commas";
  import { fmt, fmtDate } from "$lib/format";
  import type { LoanTerms, SettingsAccount } from "./types";
  import { fieldCls, inputCls, moneyPrefix, percentSuffix } from "./ui";

  // A loan's interest rate and monthly payment, as fields of its row (they sit in the row's grid): for the retirement
  // planner. Each figure Plaid supplies is the lender's and shown as is; what it leaves out (a new loan's payment, say)
  // can be set here, and saves as you change it (an empty payment is worked out from recent payments).
  let { a, loan }: { a: SettingsAccount; loan: LoanTerms } = $props();

  // svelte-ignore state_referenced_locally
  let rate = $state<number | null>(loan.set_rate ?? null);
  // svelte-ignore state_referenced_locally
  let payment = $state<number | null>(loan.set_payment ?? null);

  // What an empty payment means: the one that pays the loan off by Plaid's payoff date, else what recent payments suggest.
  const byPayoff = $derived(loan.source === "plaid" && !loan.plaid_payment && loan.payment != null && loan.set_payment == null);
  const dollars = (n: number) => Math.round(n).toLocaleString("en-US");
  const hint = $derived(byPayoff ? `${dollars(loan.payment!)} to pay it off by ${fmtDate(loan.maturity!, { month: "short", day: "numeric", year: "numeric" })}`
    : loan.inferred_payment ? `${dollars(loan.inferred_payment)} from recent payments` : "");
  const hintTitle = $derived(byPayoff ? "Left empty, it’s the payment that pays the loan off by the date the lender gives, through Plaid"
    : "Left empty, it’s worked out from the payments into this account lately");

  async function saveLoan() {
    const body: Record<string, unknown> = {};
    if (!loan.plaid) body.interest_rate = rate ?? "";
    if (!loan.plaid_payment) body.monthly_payment = payment ?? "";
    await api(`/api/accounts/${encodeURIComponent(a.id)}`, { method: "POST", body });
  }
</script>

<!-- Each figure Plaid supplies is the lender's and shown as is; what it leaves out (a new loan's payment, say) can be set. -->
{#if loan.plaid}
  <div class={fieldCls}>Interest rate
    <span class="flex h-9 items-center gap-2 text-foreground">{+(loan.rate ?? 0).toFixed(3)}%<span class="text-xs text-muted-foreground">from Plaid</span></span>
  </div>
{:else}
  <label class={fieldCls} title="The loan’s annual interest rate. With it, Net worth pays the loan down between balances and the retirement planner works out what’s still owed when you sell.">Interest rate
    <span class="relative">
      <input type="number" inputmode="decimal" min="0" max="30" step="0.001" class={`${inputCls} w-full pr-7`} bind:value={rate}
        placeholder="6.25" use:autosave={saveLoan} />
      <span class={percentSuffix} aria-hidden="true">%</span>
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
      <span class={moneyPrefix} aria-hidden="true">$</span>
      <input type="number" inputmode="decimal" min="0" step="0.01" class={`${inputCls} w-full pl-6`} bind:value={payment} {@attach commas}
        placeholder={hint} use:autosave={saveLoan} />
    </span>
  </label>
{/if}
