<script lang="ts">
  import { Badge } from "$lib/components/ui/badge";
  import { cn } from "$lib/utils";

  // Tabs inside a page that are routes of their own (#reports/trends, #review): links, so Back works.
  let { tabs, current, label, class: className }: {
    tabs: { href: string; label: string; id: string; badge?: number }[]; current: string; label: string; class?: string;
  } = $props();

  // On a phone the current tab may be off to the side of the scrolling track: start with it in view.
  function reveal(nav: HTMLElement) {
    const now = nav.querySelector<HTMLElement>("[aria-current]");
    if (now) nav.scrollLeft = now.offsetLeft - (nav.clientWidth - now.offsetWidth) / 2;
  }
</script>

<!-- A segmented control, as in iOS: the tabs share one rounded track and the current one is raised. It scrolls
     sideways on a phone when there are more tabs than fit. -->
<nav use:reveal aria-label={label} class={cn("mb-6 flex w-fit max-w-full gap-0.5 overflow-x-auto rounded-[10px] bg-card p-0.5 [scrollbar-width:none]", className)}>
  {#each tabs as t (t.id)}
    <a href={t.href} aria-current={t.id === current ? "page" : undefined}
      class={cn("flex shrink-0 items-center gap-1.5 rounded-lg px-3.5 py-1.5 phone:min-h-10 text-[13px] font-medium text-muted-foreground transition-colors hover:text-foreground",
        t.id === current && "bg-muted text-foreground shadow-sm")}>
      {t.label}
      {#if t.badge}<Badge class="h-4 min-w-4 px-1 text-[10px] tabular-nums">{t.badge}</Badge>{/if}
    </a>
  {/each}
</nav>
