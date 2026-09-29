// The transactions whose Amazon or Target order you've opened. Kept here rather than in the row, so an order stays open
// when the list loads again (after a change, or a sync) and when you come back to the page; each starts closed.
import { SvelteSet } from "svelte/reactivity";

export const openOrders = new SvelteSet<string>();
