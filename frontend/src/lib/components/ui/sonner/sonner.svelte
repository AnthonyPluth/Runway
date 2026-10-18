<script lang="ts">
  import { Toaster } from "svelte-sonner";
  import { viewport } from "$lib/phone.svelte";
  import { toasterProps } from "./config";

  // The app's toasts. The theme is the page's (the `dark` class on <html>, index.html), read again if it changes.
  let dark = $state(typeof document === "undefined" || document.documentElement.classList.contains("dark"));
  $effect(() => {
    const root = document.documentElement;
    const read = () => { dark = root.classList.contains("dark"); };
    read();
    const watch = new MutationObserver(read);
    watch.observe(root, { attributes: true, attributeFilter: ["class"] });
    return () => watch.disconnect();
  });

  // A tap anywhere on a toast that isn't a button dismisses it, as its close button does. A drag that ended in a click
  // (a swipe that didn't go far enough) doesn't count.
  let down: { x: number; y: number } | null = null;
  const onpointerdown = (e: PointerEvent) => { down = { x: e.clientX, y: e.clientY }; };
  const onclick = (e: MouseEvent) => {
    const t = e.target;
    if (!(t instanceof Element) || t.closest("button, a")) return;
    const toast = t.closest<HTMLElement>("[data-sonner-toast]");
    if (!toast || toast.dataset.dismissible === "false") return;
    if (down && Math.hypot(e.clientX - down.x, e.clientY - down.y) > 8) return;
    toast.querySelector<HTMLButtonElement>("[data-close-button]")?.click();
  };
</script>

<!-- (not a control itself: the toast's close button is the keyboard's way to the same thing) -->
<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
<div style="display: contents" {onpointerdown} {onclick}>
  <Toaster {...toasterProps(viewport.phone, dark ? "dark" : "light")} />
</div>
