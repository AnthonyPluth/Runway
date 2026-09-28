<script lang="ts" module>
  import { type VariantProps, tv } from "tailwind-variants";

  export const badgeVariants = tv({
    base: "inline-flex w-fit shrink-0 items-center gap-1 whitespace-nowrap rounded-full px-2 py-px text-[11px] font-medium [&>svg]:size-3",
    variants: {
      variant: {
        default: "bg-muted text-muted-foreground",
        primary: "bg-primary-wash text-primary-ink",
        warning: "bg-warning-wash text-warning",
        destructive: "bg-destructive-wash text-destructive",
      },
    },
    defaultVariants: { variant: "default" },
  });
  export type BadgeVariant = VariantProps<typeof badgeVariants>["variant"];
</script>

<script lang="ts">
  import { cn, type WithElementRef } from "$lib/utils";
  import type { HTMLAttributes } from "svelte/elements";

  let { ref = $bindable(null), class: className, variant = "default", children, ...restProps }:
    WithElementRef<HTMLAttributes<HTMLSpanElement>> & { variant?: BadgeVariant } = $props();
</script>

<span bind:this={ref} data-slot="badge" class={cn(badgeVariants({ variant }), className)} {...restProps}>
  {@render children?.()}
</span>
