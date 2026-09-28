<script lang="ts">
  import { Badge } from "$lib/components/ui/badge";
  import { cn } from "$lib/utils";

  // Tabs inside a page that are routes of their own (#reports/trends, #review): links, so Back works.
  let { tabs, current, label, class: className }: {
    tabs: { href: string; label: string; id: string; badge?: number }[]; current: string; label: string; class?: string;
  } = $props();
</script>

<nav aria-label={label} class={cn("mb-6 flex gap-1 overflow-x-auto border-b", className)}>
  {#each tabs as t (t.id)}
    <a href={t.href} aria-current={t.id === current ? "page" : undefined}
      class={cn("-mb-px flex shrink-0 items-center gap-2 border-b-2 border-transparent px-3 py-2 text-sm text-muted-foreground transition-colors hover:text-foreground",
        t.id === current && "border-primary font-medium text-foreground")}>
      {t.label}
      {#if t.badge}<Badge class="tabular-nums">{t.badge}</Badge>{/if}
    </a>
  {/each}
</nav>
