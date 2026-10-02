<script lang="ts">
  import * as Sheet from "$lib/components/ui/sheet";
  import AssetCard from "./AssetCard.svelte";
  import AssetForm from "./AssetForm.svelte";
  import { ASSET_KIND_LABEL, type Asset, type NetWorth } from "./types";

  // The side panel for one asset (`a`), or for adding one (`a` null, starting as `kind`). Viewing shows the value (one field to
  // change it) and its actions; "Edit details" swaps in the form. `open` is bound so the page can open it.
  let { open = $bindable(false), a = null, kind = "home", d, onchanged }: { open?: boolean; a?: Asset | null; kind?: string; d: NetWorth; onchanged: () => void } = $props();

  let editing = $state(false);
  // Each time the panel opens on something, start from the summary.
  $effect(() => { if (open) editing = false; });
  const title = $derived(a ? a.name : `Add ${kind === "vehicle" ? "a vehicle" : kind === "home" ? "a home" : "an asset"}`);
</script>

<Sheet.Root bind:open>
  <Sheet.Content>
    <Sheet.Header>
      <Sheet.Title>{title}</Sheet.Title>
      {#if a}<Sheet.Description>{ASSET_KIND_LABEL[a.kind] ?? a.kind}</Sheet.Description>{/if}
    </Sheet.Header>
    <div class="overflow-y-auto px-4 pb-4">
      {#if !a}
        {#key kind}<AssetForm a={null} {d} {kind} onclose={(changed) => { open = false; if (changed) onchanged(); }} />{/key}
      {:else if editing}
        {#key a.id}<AssetForm {a} {d} onclose={(changed) => { editing = false; if (changed) onchanged(); }} />{/key}
      {:else}
        <AssetCard {a} {d} onedit={() => (editing = true)} {onchanged} onremoved={() => (open = false)} />
      {/if}
    </div>
  </Sheet.Content>
</Sheet.Root>
