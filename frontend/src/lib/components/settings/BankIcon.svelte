<script lang="ts" module>
  // The same institution matching as runway/brands.py, for names the server hasn't matched to an account (Plaid connections).
  const PATTERNS: [RegExp, string][] = [[/\bchase\b|jpmorgan/, "chase"], [/capital ?one/, "capital-one"], [/\bciti/, "citibank"],
    [/american express|\bamex\b/, "american-express"], [/\bdiscover\b/, "discover-card"], [/bank of america|\bbofa\b|merrill/, "bank-of-america"],
    [/wells ?fargo/, "wells-fargo"], [/\bu\.? ?s\.? bank\b/, "u-s-bank"], [/navy federal/, "navy-federal-credit-union"], [/\busaa\b/, "usaa"],
    [/fidelity/, "fidelity"], [/vanguard/, "vanguard"], [/schwab/, "charles-schwab"], [/e\*? ?trade/, "e-trade"],
    [/interactive brokers/, "interactive-brokers"], [/robinhood/, "robinhood"], [/\bsofi\b/, "sofi"], [/paypal/, "paypal"]];
  export function brandFor(name: string): string | null {
    const n = name.toLowerCase();
    return PATTERNS.find(([re]) => re.test(n))?.[1] ?? null;
  }
</script>

<script lang="ts">
  import { app } from "$lib/app.svelte";

  // An institution's logo: by account id (the brands in /api/state), or by institution name (a Plaid connection).
  // Falls back to the first letter, or "?" when Runway doesn't know the bank.
  let { id, name }: { id?: string; name?: string | null } = $props();
  const brand = $derived(id ? app.state?.brands?.[id] : undefined);
  const logo = $derived(id ? brand?.logo : brandFor(name || ""));
  const letter = $derived(id ? brand?.initial || "?" : (name || "?").replace(/[^A-Za-z0-9]/g, "").slice(0, 1).toUpperCase() || "?");
  const title = $derived(id ? brand?.institution ?? "" : "");
</script>

{#if logo}
  <img class="size-7 shrink-0 rounded-md bg-white object-contain p-0.5" src={`/banks/${logo}.svg`} alt="" {title} width="28" height="28" loading="lazy" />
{:else}
  <span class="flex size-7 shrink-0 items-center justify-center rounded-md bg-muted text-xs font-semibold text-muted-foreground" {title}
    aria-hidden="true">{letter}</span>
{/if}
