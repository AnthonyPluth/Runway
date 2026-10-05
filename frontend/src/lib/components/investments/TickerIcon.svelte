<script lang="ts">
  // A held stock's or fund's logo, which the investments API gives only once Runway has fetched one from Logo.dev (by ticker,
  // else by the fund family; runway/domain/merchants.py holding_logos). A letter without one, for cash, and if the image fails.
  let { ticker, name, logo = null }: { ticker: string | null; name: string | null; logo?: string | null } = $props();
  let failed = $state<string | null>(null);   // the logo that failed to load
  const symbol = $derived(ticker && !ticker.includes(":") ? ticker.trim().toUpperCase() : "");
  const letter = $derived((symbol || name || "?").replace(/[^A-Za-z0-9]/g, "").slice(0, 1).toUpperCase() || "?");
</script>

{#if logo && failed !== logo}
  <img class="mt-0.5 size-6 shrink-0 rounded-md object-contain" src={logo} alt="" width="24" height="24" loading="lazy" onerror={() => (failed = logo)} />
{:else}
  <span class="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-md bg-muted text-xs font-semibold text-muted-foreground" aria-hidden="true">{letter}</span>
{/if}
