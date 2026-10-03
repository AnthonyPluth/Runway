<script lang="ts">
  import * as Sheet from "$lib/components/ui/sheet";
  import { EQ_KINDS } from "./equity";
  import GrantForm from "./GrantForm.svelte";
  import type { Grant, GrantBody } from "./types";

  // The side panel for adding a grant (`g` null) or editing one. `open` is bound so the page can open it and close it
  // once the save goes through; `onsave` answers with why it didn't, or null.
  let { open = $bindable(false), g = null, company = "", onsave }: { open?: boolean; g?: Grant | null; company?: string; onsave: (body: GrantBody) => Promise<string | null> } = $props();
</script>

<Sheet.Root bind:open>
  <Sheet.Content>
    <Sheet.Header>
      <Sheet.Title>{g ? "Edit grant" : "Add a grant"}</Sheet.Title>
      <Sheet.Description>{company}{g ? ` · ${g.label || EQ_KINDS[g.kind]}` : ""}</Sheet.Description>
    </Sheet.Header>
    {#key g?.id ?? "new"}
      <GrantForm {g} {onsave} oncancel={() => (open = false)} />
    {/key}
  </Sheet.Content>
</Sheet.Root>
