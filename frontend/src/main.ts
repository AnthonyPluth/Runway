import { mount } from "svelte";
import "./app.css";
import App from "./App.svelte";
import { boot, whenBooted } from "$lib/app.svelte";
import { resumePlaidOAuth } from "$lib/components/settings/plaid.svelte";

const app = mount(App, { target: document.getElementById("app")! });
boot();

// Back from a bank's own sign-in page (Plaid OAuth): finish linking the account once Runway has answered.
if (location.pathname === "/plaid/oauth") whenBooted(() => resumePlaidOAuth());

// Installable app: the service worker shows notifications and keeps the app's shell for offline starts.
if ("serviceWorker" in navigator && window.isSecureContext) {
  navigator.serviceWorker.register("/sw.js").catch((err) => console.warn("Service worker:", err));
}

export default app;
