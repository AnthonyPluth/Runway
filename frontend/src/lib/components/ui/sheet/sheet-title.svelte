<script lang="ts">
  import { cn } from "$lib/utils";
  import { Dialog } from "bits-ui";

  let { ref = $bindable(null), class: className, ...restProps }: Dialog.TitleProps = $props();
</script>

<Dialog.Title bind:ref data-slot="sheet-title" class={cn("text-foreground font-semibold", className)} {...restProps} />
