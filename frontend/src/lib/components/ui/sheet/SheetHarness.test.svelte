<script lang="ts">
  import * as Sheet from "./index";

  let { open = $bindable(false), onOpenChange }: { open?: boolean; onOpenChange?: (o: boolean) => void } = $props();
</script>

<Sheet.Root bind:open {onOpenChange}>
  <Sheet.Trigger>Edit</Sheet.Trigger>
  <Sheet.Content>
    <Sheet.Header>
      <Sheet.Title>Edit asset</Sheet.Title>
      <Sheet.Description>Change the details.</Sheet.Description>
    </Sheet.Header>
    <Sheet.Footer>
      <Sheet.Close>Cancel</Sheet.Close>
    </Sheet.Footer>
  </Sheet.Content>
</Sheet.Root>
