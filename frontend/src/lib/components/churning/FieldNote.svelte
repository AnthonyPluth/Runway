<script lang="ts">
  import type { FormErrors } from "./validate";

  // What's wrong with one field, under it; the field points at it with aria-describedby (see `fieldProps`).
  let { uid, name, errors }: { uid: string; name: string; errors: FormErrors } = $props();
</script>

{#if errors[name]}<p id="{uid}-{name}-err" class="text-xs text-destructive">{errors[name]}</p>{/if}
