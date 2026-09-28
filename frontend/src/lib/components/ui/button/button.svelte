<script lang="ts" module>
  import { cn, type WithElementRef } from "$lib/utils";
  import type { HTMLAnchorAttributes, HTMLButtonAttributes } from "svelte/elements";
  import { type VariantProps, tv } from "tailwind-variants";

  export const buttonVariants = tv({
    base: "inline-flex shrink-0 cursor-pointer items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium outline-none transition-colors focus-visible:ring-[3px] focus-visible:ring-ring/40 disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground font-semibold hover:bg-primary/85",
        secondary: "bg-secondary text-secondary-foreground ring-1 ring-border hover:bg-accent hover:text-accent-foreground",
        ghost: "hover:bg-accent hover:text-accent-foreground",
        destructive: "bg-destructive text-white hover:bg-destructive/85",
        link: "text-primary-ink underline-offset-4 hover:underline",
      },
      size: {
        default: "h-9 px-4 py-2",
        sm: "h-8 gap-1.5 rounded-md px-3 text-[13px]",
        xs: "h-auto px-1.5 py-0.5 text-[13px]",
        icon: "size-9",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  });

  export type ButtonVariant = VariantProps<typeof buttonVariants>["variant"];
  export type ButtonSize = VariantProps<typeof buttonVariants>["size"];
  export type ButtonProps = WithElementRef<HTMLButtonAttributes> &
    WithElementRef<HTMLAnchorAttributes> & { variant?: ButtonVariant; size?: ButtonSize };
</script>

<script lang="ts">
  let { class: className, variant = "default", size = "default", ref = $bindable(null), href = undefined,
    type = "button", disabled, children, ...restProps }: ButtonProps = $props();
</script>

{#if href}
  <a bind:this={ref} class={cn(buttonVariants({ variant, size }), className)} href={disabled ? undefined : href}
    aria-disabled={disabled} role={disabled ? "link" : undefined} tabindex={disabled ? -1 : undefined} {...restProps}>
    {@render children?.()}
  </a>
{:else}
  <button bind:this={ref} class={cn(buttonVariants({ variant, size }), className)} {type} {disabled} {...restProps}>
    {@render children?.()}
  </button>
{/if}
