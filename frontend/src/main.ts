import { mount } from "svelte";
import "./app.css";
import App from "./App.svelte";
import { boot, whenBooted } from "$lib/app.svelte";
import { prefetchOverview } from "$lib/prefetch";

// Overview's data doesn't need the state first: ask for both at once (lib/prefetch.ts).
prefetchOverview();
const app = mount(App, { target: document.getElementById("app")! });
boot();

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
