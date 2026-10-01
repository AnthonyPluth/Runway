<script lang="ts">
  import { api } from "$lib/api";
  import { Button } from "$lib/components/ui/button";
  import { cn } from "$lib/utils";
  import { toast } from "svelte-sonner";
  import type { Snippet } from "svelte";

  // The merchant's logo, which (tucked away: click it) lets you choose another for every transaction from that
  // merchant: one of Logo.dev's matches for its name, a website's logo, none, or Runway's own pick again. With
  // `account` it's an account's logo instead (Settings → Accounts), whose matches are for its institution.
  let { name, children, onchanged, account }: { name: string; children: Snippet; onchanged: () => void; account?: string } = $props();
  const base = $derived(account ? `/api/accounts/${encodeURIComponent(account)}` : "/api/merchants");
  const which = $derived(account ? "this account" : "this merchant");

  interface Options { choice: { website: string | null; hidden: boolean } | null; searchable: boolean; configured: boolean;
    candidates: { name: string; domain: string }[]; error: string | null }
  let open = $state(false);
  let opts = $state<Options | null>(null);
  let website = $state("");
  let busy = $state(false);
  let root = $state<HTMLElement>();
  let panel = $state<HTMLElement>();
  // The panel floats over the page (a grouped list clips what spills out of it): under the logo, or above it when
  // there isn't room below, and kept inside the window.
  let pos = $state({ top: 0, left: 0 });
  function place() {
    if (!root) return;
    const r = root.getBoundingClientRect(), h = panel?.offsetHeight ?? 260, w = panel?.offsetWidth ?? 320;
    const below = r.bottom + 8, above = r.top - 8 - h;
    pos = { top: below + h > window.innerHeight - 8 && above > 8 ? above : below,
            left: Math.max(8, Math.min(r.left, window.innerWidth - w - 8)) };
  }
  $effect(() => { if (open) { void opts; requestAnimationFrame(place); } });

  async function show() {
    open = !open;
    if (!open) return;
    opts = null;
    try { opts = await api<Options>(account ? `${base}/logo-options` : `${base}/logo-options?name=${encodeURIComponent(name)}`); website = opts.choice?.website ?? ""; }
    catch (err) { toast.error((err as Error).message); open = false; }
  }
  async function choose(body: { website?: string; hidden?: boolean }, what: string) {
    busy = true;
    try {
      await api(`${base}/logo`, { method: "POST", body: account ? body : { name, ...body } });
      toast.success(account ? `${what} for ${name}` : `${what} for every ${name} transaction`); open = false; onchanged();
    } catch (err) { toast.error((err as Error).message); }
    finally { busy = false; }
  }
  function outside(e: MouseEvent) { if (open && root && !root.contains(e.target as Node)) open = false; }
</script>

<svelte:window onclick={outside} onkeydown={(e) => { if (open && e.key === "Escape") open = false; }}
  onscroll={() => open && place()} onresize={() => open && place()} />

<span class="relative block" bind:this={root}>
  <button type="button" class="block cursor-pointer rounded-lg focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
    title={`Change ${which}'s logo`} aria-label={`Logo for ${name}`} aria-expanded={open} onclick={show}>
    {@render children()}
  </button>
  {#if open}
    <div data-editor role="dialog" aria-label={`Logo for ${name}`} bind:this={panel}
      style:top={`${pos.top}px`} style:left={`${pos.left}px`}
      class="fixed z-50 w-80 max-w-[calc(100vw-1rem)] rounded-xl bg-popover p-3 text-sm text-popover-foreground shadow-xl ring-1 ring-border">
      <div class="mb-2 font-medium">Logo for {name}</div>
      {#if !opts}
        <p class="text-muted-foreground">Loading…</p>
      {:else}
        {#if opts.candidates.length}
          <div class="mb-1 text-xs text-muted-foreground">Logo.dev's matches</div>
          <div class="mb-3 flex flex-col">
            {#each opts.candidates as c (c.domain)}
              <button type="button" disabled={busy} onclick={() => choose({ website: c.domain }, `Using ${c.domain}'s logo`)}
                class={cn("flex cursor-pointer justify-between gap-3 rounded-md px-2 py-1.5 text-left hover:bg-muted", opts.choice?.website === c.domain && "bg-muted")}>
                <span class="truncate">{c.name || c.domain}</span><span class="shrink-0 text-muted-foreground">{c.domain}</span>
              </button>
            {/each}
          </div>
        {:else if opts.error}
          <p class="mb-2 text-xs text-destructive">{opts.error}</p>
        {/if}
        {#if opts.configured}
          <form class="flex gap-2" onsubmit={(e) => { e.preventDefault(); if (website.trim()) choose({ website }, `Using ${website.trim()}'s logo`); }}>
            <input class="h-9 min-w-0 flex-1 rounded-lg bg-input px-2.5 outline-none focus-visible:ring-2 focus-visible:ring-ring"
              placeholder={account ? "A website, e.g. chase.com" : "Its website, e.g. target.com"}
              aria-label={account ? "The website whose logo to use" : "The merchant's website"} bind:value={website} />
            <Button type="submit" size="sm" disabled={busy || !website.trim()}>Use</Button>
          </form>
        {:else}
          <p class="text-xs text-muted-foreground">Add a Logo.dev key in Settings → Services to pick logos by website.</p>
        {/if}
        <div class="mt-3 flex items-center justify-between">
          <Button variant="link" size="sm" class="px-0" disabled={busy || opts.choice?.hidden} onclick={() => choose({ hidden: true }, "No logo")}>No logo</Button>
          <Button variant="link" size="sm" class="px-0" disabled={busy || !opts.choice} onclick={() => choose({}, "Automatic logo")}>Automatic</Button>
        </div>
      {/if}
    </div>
  {/if}
</span>
