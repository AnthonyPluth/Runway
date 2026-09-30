<script lang="ts">
  import { Dialog } from "bits-ui";

  // A side panel for editing: slides in from the right on desktop, rises as a bottom sheet on phones
  // (see sheet-content.svelte). Built on Bits UI's Dialog, so Esc, backdrop click, focus trapping and
  // focus restore come for free. Drive it with `bind:open` or `onOpenChange`, or use Sheet.Trigger.
  let { open = $bindable(false), onOpenChange, children }: Dialog.RootProps = $props();
</script>

<Dialog.Root bind:open {onOpenChange} {children} />
