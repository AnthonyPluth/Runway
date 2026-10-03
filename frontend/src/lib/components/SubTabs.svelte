<script lang="ts">
  import { Badge } from "$lib/components/ui/badge";
  import { cn } from "$lib/utils";

  // Tabs inside a page that are routes of their own (#reports/trends, #review): links, so Back works.
  let { tabs, current, label, class: className }: {
    tabs: { href: string; label: string; id: string; badge?: number }[]; current: string; label: string; class?: string;
  } = $props();

  // On a phone the current tab may be off to the side of the scrolling track: start with it in view. While there's more
  // of the track to one side, that edge fades out, so it's clear the tabs go on.
  function reveal(nav: HTMLElement) {
    const now = nav.querySelector<HTMLElement>("[aria-current]");
    if (now) nav.scrollLeft = now.offsetLeft - (nav.clientWidth - now.offsetWidth) / 2;
    const edges = () => {
      const more = nav.scrollWidth - nav.clientWidth;
      nav.style.setProperty("--fade-start", more > 1 && nav.scrollLeft > 1 ? "24px" : "0px");
      nav.style.setProperty("--fade-end", more > 1 && nav.scrollLeft < more - 1 ? "24px" : "0px");
    };
    edges();
    nav.addEventListener("scroll", edges, { passive: true });
    const ro = typeof ResizeObserver === "function" ? new ResizeObserver(edges) : null;
    ro?.observe(nav);
    return { destroy() { nav.removeEventListener("scroll", edges); ro?.disconnect(); } };
  }
</script>

<!-- A segmented control, as in iOS: the tabs share one rounded track and the current one is raised. It scrolls
     sideways on a phone when there are more tabs than fit. -->
<nav use:reveal aria-label={label} class={cn("subtabs mb-6 flex w-fit max-w-full gap-0.5 overflow-x-auto rounded-[10px] bg-card p-0.5 [scrollbar-width:none]", className)}>
  {#each tabs as t (t.id)}
    <a href={t.href} aria-current={t.id === current ? "page" : undefined}
      class={cn("flex shrink-0 items-center gap-1.5 rounded-lg px-3.5 py-1.5 phone:min-h-10 text-[13px] font-medium text-muted-foreground transition-colors hover:text-foreground",
        t.id === current && "bg-muted text-foreground shadow-sm")}>
      {t.label}
      {#if t.badge}<Badge class="h-4 min-w-4 px-1 text-[10px] tabular-nums">{t.badge}</Badge>{/if}
    </a>
  {/each}
</nav>

<style>
  .subtabs { --fade-start: 0px; --fade-end: 0px; mask-image: linear-gradient(to right, transparent, #000 var(--fade-start), #000 calc(100% - var(--fade-end)), transparent); }
</style>
