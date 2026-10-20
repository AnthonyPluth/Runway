<script lang="ts">
  import { cn } from "$lib/utils";
  import type { Snippet } from "svelte";

  // A titled grouped list (see .group-list in app.css): a small caps heading, an optional action on its right, and
  // the rows. `href` makes the title a link (named `label` if given). `inset` is where the hairline between rows starts (past a row's icon).
  let { title, action, children, footer, inset = "1rem", href, label, class: className }: {
    title?: string; action?: Snippet; children: Snippet; footer?: string; inset?: string; href?: string; label?: string; class?: string;
  } = $props();
</script>

<section class={cn("min-w-0", className)}>
  {#if title || action}
    <div class="mb-1.5 flex items-baseline justify-between px-4">
      {#if title}<h2 class="eyebrow">{#if href}<a {href} aria-label={label} class="hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none">{title}</a>{:else}{title}{/if}</h2>{/if}
      {@render action?.()}
    </div>
  {/if}
  <div class="group-list" style:--inset={inset}>{@render children()}</div>
  {#if footer}<p class="mt-1.5 px-4 text-xs text-muted-foreground">{footer}</p>{/if}
</section>
