<script lang="ts">
  import { isDark } from "$lib/logoTone";

  // A merchant's logo as its brand draws it, on white only when it's too dark to read on the dark page.
  let { src, size = 32, class: cls = "" }: { src: string; size?: number; class?: string } = $props();
  let dark = $state(false);
</script>

<img class={`shrink-0 rounded-lg object-contain ${dark ? "bg-white p-0.5" : ""} ${cls}`} style:width={`${size}px`}
  style:height={`${size}px`} {src} alt="" loading="lazy" width={size} height={size} onload={(e) => (dark = isDark(e.currentTarget as HTMLImageElement))} />
