# Runway's browser extension: Amazon, Target, Costco and Carta

This extension reads your Amazon, Target and Costco orders and your Carta grants with the sign-in already in your
browser, and sends them to **your** Runway, which splits each card charge by what you bought.

To install it: in Runway, open **Settings → Connections**, click **Make a key** and copy it. Then go to
`chrome://extensions`, turn on **Developer mode**, click **Load unpacked** and choose this folder. On the options page
that opens, enter Runway's address and the key, then **Save and test**.

The full guide (what it reads, how it stays out of sight, sign-in proxies, and what to do when a store changes its
site) is at https://anthonypluth.github.io/Runway/using/browser-extension/

## Layout

`background.js` runs the imports and the daily alarm; the rest is split by job, as plain scripts that share one global
scope (no build step). Chrome's service worker loads them with `importScripts` at the top of `background.js`, and
Firefox from `background.scripts` in `manifest.json`: keep the two lists the same, in the same order (a test checks).

- `runway.js`: the Runway address and key from Options, the status the popup shows, and calls to Runway.
- `stores.js`: the allow-list of store addresses the extension may read or send a page to (`storeUrl`).
- `frames.js`: hidden frames (Chrome's offscreen document, Firefox's background page) and the background-tab fallback.
- `amazon.js`, `target.js`, `costco.js`, `carta.js`: one importer per store.
- `util.js`: timeouts, `inParallel` and other small helpers.
- `page.js`, `frame.js`: what runs inside a store's page; `offscreen.js`: Chrome's hidden page.

The pure helpers are tested with Vitest (`frontend/src/extension/`: `cd frontend && npm test`).
