<script lang="ts">
  // The little colored square that ties a row or legend entry to its color in a chart.
  let { color }: { color: string } = $props();
</script>

<i aria-hidden="true" class="mr-[7px] inline-block size-2.5 shrink-0 rounded-[3px] align-[-1px]" style:background={color}></i>
