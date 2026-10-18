// How the app's toasts look and where they sit. Kept apart from the component so a test can read it without drawing one.
import { buttonVariants } from "$lib/components/ui/button/button.svelte";
import type { ToasterProps } from "svelte-sonner";

/** A plain toast stays this long, in ms. A toast you can act on (Undo, Always) sets its own, longer one. */
export const TOAST_MS = 6000;

/** Above the phone tab bar by this much (MobileNav: `--tabbar-h` in app.css is its height). */
export const BAR_GAP = "0.5rem";
/** Off the bottom edge when there is no tab bar (the tablet rail, a desktop), clear of a home indicator. */
export const EDGE_GAP = "0.75rem";

/** The distance from the bottom of the screen to the toasts. */
export function toastBottom(phone: boolean): string {
  return phone ? `calc(var(--tabbar-h) + ${BAR_GAP})` : `calc(env(safe-area-inset-bottom) + ${EDGE_GAP})`;
}

// A toast is its text (and an icon) on one row and its buttons, if it has any, on the next, so a long button label
// ("Always for Corner Market") never squeezes the text. It is drawn by these classes alone (`unstyled`), from the same
// tokens as a card or popover, so it follows the theme (app.css `:root` / `.dark`) and nothing here names a colour. Its
// buttons are the app's own (ui/button).
export const TOAST_CLASSES = {
  toast: "pointer-events-auto relative flex min-h-[3.25rem] w-[var(--width)] flex-wrap items-center justify-end gap-x-3 gap-y-2 rounded-xl border bg-popover py-3 pr-14 pl-4 text-sm text-popover-foreground shadow-lg",
  icon: "flex shrink-0 items-center [&_svg]:size-4",
  content: "flex min-w-0 flex-[1_1_calc(100%-2rem)] flex-col gap-0.5",
  title: "font-medium",
  description: "text-xs text-muted-foreground",
  actionButton: buttonVariants({ variant: "default", size: "sm" }),
  cancelButton: buttonVariants({ variant: "outline", size: "sm" }),
  // Always there (not on hover) and 44px square, the size a finger can hit, in the toast's top right corner.
  closeButton: "absolute top-1 right-1 inline-flex size-11 shrink-0 cursor-pointer items-center justify-center rounded-md text-muted-foreground outline-none hover:bg-accent hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50 [&_svg]:size-5",
  success: "[&_[data-icon]]:text-good",
  error: "[&_[data-icon]]:text-destructive",
  warning: "[&_[data-icon]]:text-warning",
} as const;

/** The Toaster's props. `phone`: the tab bar is showing (lib/phone.svelte.ts), so the toasts sit above it. */
export function toasterProps(phone: boolean, theme: "light" | "dark"): ToasterProps {
  const bottom = toastBottom(phone);
  return {
    theme,
    position: "bottom-center",
    // Sonner swaps between the two at 600px, but the bar shows up to 767px (and on a phone turned sideways): the same
    // bottom for both, so it follows the bar and not sonner's breakpoint.
    offset: { bottom },
    mobileOffset: { bottom },
    closeButton: true,
    closeButtonAriaLabel: "Dismiss",
    duration: TOAST_MS,
    swipeDirections: ["left", "right", "bottom"],
    toastOptions: { unstyled: true, classes: TOAST_CLASSES },
  };
}
