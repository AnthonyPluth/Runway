<script lang="ts">
  import { cn } from "$lib/utils";

  // The pulsing green dot next to a live price.
  let { class: className, label, title }: { class?: string; label?: string; title?: string } = $props();
</script>

<i class={cn("live-dot inline-block size-2 shrink-0 rounded-full bg-[var(--good)]", className)} {title}
  role={label ? "img" : undefined} aria-label={label} aria-hidden={label ? undefined : "true"}></i>

<style>
  .live-dot { animation: live-pulse 2s ease-in-out infinite; }
  @keyframes live-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.35; } }
  @media (prefers-reduced-motion: reduce) { .live-dot { animation: none; } }
</style>
