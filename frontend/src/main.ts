import { mount } from "svelte";
import "./app.css";
import App from "./App.svelte";
import { boot, whenBooted } from "$lib/app.svelte";
import { whenUnlocked } from "$lib/lock.svelte";
import { prefetchOverview } from "$lib/prefetch";

// With the app lock on (lib/lock.svelte.ts), App draws the lock screen first and nothing else, and Runway is asked for
// no data, not even Overview's early request, until it's unlocked. Without it, all of this starts at once.
// Overview's data doesn't need the state first: ask for both at once (lib/prefetch.ts).
whenUnlocked(prefetchOverview);
const app = mount(App, { target: document.getElementById("app")! });
whenUnlocked(boot);

// Back from a bank's own sign-in page (Plaid OAuth): finish linking the account once Runway has answered.
if (location.pathname === "/plaid/oauth") whenBooted(() => {
  // Only this one URL needs the Plaid code, so it loads then, not with every page.
  import("$lib/components/settings/plaid.svelte").then((m) => m.resumePlaidOAuth()).catch((err) => console.error(err));
});

// Installable app: the service worker shows notifications and keeps the app's shell for offline starts.
if ("serviceWorker" in navigator && window.isSecureContext) {
  navigator.serviceWorker.register("/sw.js").catch((err) => console.warn("Service worker:", err));
}

export default app;
