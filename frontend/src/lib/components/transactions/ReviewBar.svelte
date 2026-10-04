<script lang="ts">
  import { app } from "$lib/app.svelte";
  import { Button } from "$lib/components/ui/button";
  import { ConfirmDialog } from "$lib/components/ui/confirm-dialog";
  import { plural } from "$lib/format";
  import type { ReviewMode } from "./review.svelte";
  import Switch from "./Switch.svelte";

  // To review's own line, under the filters: accept what's certain at once, review a merchant at a time, and (desktop)
  // the keys. More than a few at once asks first.
  let { rv }: { rv: ReviewMode } = $props();
</script>

{#if rv.txs.list?.items.length}
  <div class="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1">
    {#if rv.acceptable.length && !rv.grouped}
      <Button variant="outline" size="sm" onclick={rv.startAcceptAll}
        title={rv.sure ? "Keep the categories it’s at least 90% sure of" : "Keep every category suggested so far"}>
        {rv.sure ? "Accept all ≥ 90%" : "Accept all suggestions"} <span class="tabular-nums text-muted-foreground">{rv.acceptable.length}</span></Button>
    {/if}
    {#if !app.state?.has_api_key}
      <div class="w-48"><Switch checked={rv.grouped} label="Group by merchant" onchange={(on) => { rv.grouped = on; rv.keyed = ""; }} /></div>
    {/if}
    {#if !rv.grouped}
      <p class="ml-auto text-xs text-muted-foreground max-md:hidden [@media(hover:none)]:hidden [&_kbd]:rounded [&_kbd]:border [&_kbd]:px-1 [&_kbd]:font-sans" data-keys-hint>
        <kbd>j</kbd>/<kbd>k</kbd> move · <kbd>Enter</kbd> accept · <kbd>c</kbd> category</p>
    {/if}
  </div>
{/if}

{#if rv.askAll}
  <ConfirmDialog bind:open={rv.askAll} title={`Accept ${plural(rv.acceptable.length, "transaction")}?`}
    description="They keep their categories and leave To review. You can undo it afterwards." confirmLabel="Accept" busyLabel="Accepting…"
    onconfirm={rv.acceptAll} />
{/if}
