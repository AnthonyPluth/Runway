<script lang="ts">
  import { cn } from "$lib/utils";
  import { Dialog } from "bits-ui";

  let { ref = $bindable(null), class: className, ...restProps }: Dialog.DescriptionProps = $props();
</script>

<Dialog.Description bind:ref data-slot="sheet-description" class={cn("text-muted-foreground text-sm", className)} {...restProps} />
