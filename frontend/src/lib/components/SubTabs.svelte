<script lang="ts">
  import { Badge } from "$lib/components/ui/badge";
  import { cn } from "$lib/utils";

  // Tabs inside a page that are routes of their own (#reports/trends, #review): links, so Back works.
  let { tabs, current, label, class: className }: {
    tabs: { href: string; label: string; id: string; badge?: number }[]; current: string; label: string; class?: string;
  } = $props();
</script>

<!-- The line under the tabs is an inset shadow, not a border: the current tab's underline sits on it without spilling
     out of the strip, which (as it scrolls sideways on a phone) would otherwise show a vertical scrollbar. -->
<nav aria-label={label} class={cn("mb-6 flex gap-1 overflow-x-auto shadow-[inset_0_-1px_0_var(--border)]", className)}>
  {#each tabs as t (t.id)}
    <a href={t.href} aria-current={t.id === current ? "page" : undefined}
      class={cn("flex shrink-0 items-center gap-2 border-b-2 border-transparent px-3 py-2 text-sm text-muted-foreground transition-colors hover:text-foreground",
        t.id === current && "border-primary font-medium text-foreground")}>
      {t.label}
      {#if t.badge}<Badge class="tabular-nums">{t.badge}</Badge>{/if}
    </a>
  {/each}
</nav>
