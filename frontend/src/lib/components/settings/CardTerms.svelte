<script lang="ts">
  import { commas } from "$lib/commas";
  import { autosave } from "$lib/autosave";
  import { accountName } from "$lib/types";
  import type { SettingsAccount } from "./types";
  import { fieldCls, inputCls, moneyPrefix, percentSuffix, selectCls } from "./ui";

  // A credit card's payment settings, as fields of its row (they sit in the row's grid): the account that pays it, how
  // the forecast pays each statement (in full, the minimum, or a fixed amount) and its APR, for the interest on what that
  // leaves to carry over. Each saves as you change it (`save`, with whether the page should redraw after).
  let { a, cash, payFrom = $bindable(), payMode = $bindable(), payAmount = $bindable(), apr = $bindable(), save }: {
    a: SettingsAccount; cash: SettingsAccount[]; payFrom: string; payMode: string; payAmount: number | null; apr: number | null;
    save: (rerender: boolean) => Promise<void>;
  } = $props();
</script>

<label class={fieldCls}>Paid from
  <select class={selectCls} bind:value={payFrom} use:autosave={() => save(true)}>
    <option value="">—</option>
    {#each cash as c (c.id)}<option value={c.id}>{accountName(c)}</option>{/each}
  </select>
</label>
<label class={fieldCls} title="How much of each statement the forecast pays; what isn't paid carries into the next one">Pay
  <select class={selectCls} bind:value={payMode} use:autosave={() => save(true)}>
    <option value="full">full statement</option>
    <option value="minimum">minimum</option>
    <option value="fixed">a fixed amount</option>
  </select>
</label>
{#if payMode === "fixed"}
  <label class={fieldCls}>Amount each statement
    <span class="relative">
      <span class={moneyPrefix} aria-hidden="true">$</span>
      <input type="number" inputmode="decimal" min="0" step="0.01" class={`${inputCls} w-full pl-6`} bind:value={payAmount} {@attach commas}
        use:autosave={() => save(true)} />
    </span>
  </label>
{/if}
{#if payMode !== "full"}
  <!-- Yours wins; without one, the issuer's purchase APR (through Plaid) is used, and shown as the placeholder. -->
  <div class={fieldCls} title="For the interest on what carries over; without one, the forecast leaves interest out">
    <label for={`apr-${a.id}`}>APR</label>
    <span class="relative">
      <input id={`apr-${a.id}`} type="number" inputmode="decimal" min="0" max="100" step="0.01" class={`${inputCls} w-full pr-7`} bind:value={apr}
        placeholder={a.issuer_apr != null ? String(a.issuer_apr) : undefined} use:autosave={() => save(false)} />
      <span class={percentSuffix} aria-hidden="true">%</span>
    </span>
    {#if apr == null && a.issuer_apr != null}<span class="text-xs">{a.issuer_apr}% from the issuer</span>{/if}
  </div>
{/if}
