<script lang="ts" module>
  // Where Runway serves the Logo.dev logo it fetched for an institution's name (runway/domain/merchants.py logo_path).
  export const logoFor = (name: string) => `/api/merchants/${encodeURIComponent(`brand:${name.toLowerCase().split(/\s+/).filter(Boolean).join(" ")}`)}/logo`;
</script>

<script lang="ts">
  import { app } from "$lib/app.svelte";

  // An institution's logo (Logo.dev's): by account id (the brands in /api/state, which have one once Runway has
  // fetched it, or the one you chose), or by institution name (a Plaid connection: the state's connection_logos, else
  // asked for by name). Falls back to the first letter, or "?" when there's no name.
  let { id, name }: { id?: string; name?: string | null } = $props();
  let failed = $state(false);   // a name's logo may not have been fetched yet
  const brand = $derived(id ? app.state?.brands?.[id] : undefined);
  const known = $derived(name ? app.state?.connection_logos?.[name] : undefined);
  const src = $derived(id ? brand?.src : !name || !app.state?.logodev_configured ? null : known !== undefined ? known : logoFor(name));
  const letter = $derived(id ? brand?.initial || "?" : (name || "?").replace(/[^A-Za-z0-9]/g, "").slice(0, 1).toUpperCase() || "?");
  const title = $derived(id ? brand?.institution ?? "" : "");
</script>

{#if src && !failed}
  <img class="size-7 shrink-0 rounded-md object-contain" src={src} alt="" {title} width="28" height="28" loading="lazy" onerror={() => (failed = true)} />
{:else}
  <span class="flex size-7 shrink-0 items-center justify-center rounded-md bg-muted text-xs font-semibold text-muted-foreground" {title}
    aria-hidden="true">{letter}</span>
{/if}
