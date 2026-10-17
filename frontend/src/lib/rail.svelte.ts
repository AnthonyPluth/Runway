// Tablet width: 768 through 1023 px, where the sidebar shrinks to a 72px rail. The same query is the `rail:` variant in app.css,
// so CSS and code agree. A phone turned sideways is a phone (lib/phone.svelte.ts), however wide it is.
export const RAIL_QUERY = "(min-width: 768px) and (max-width: 1023px) and (not ((max-height: 500px) and (pointer: coarse)))";

const mq = typeof matchMedia === "function" ? matchMedia(RAIL_QUERY) : null;

/** Live and reactive: `tablet.rail` is true while the navigation is the compact rail. */
export const tablet = $state({ rail: mq?.matches ?? false });
mq?.addEventListener("change", (e) => { tablet.rail = e.matches; });
