// Runway's service worker: shows push notifications, and keeps the app's shell around so it opens without a
// connection (with the last-loaded page). Data always comes fresh from the server; nothing from /api is cached.
const CACHE = "runway-shell-v6";
// The app's page and Runway's own files. The app's built files (/assets/…) are kept as the page loads them: their
// names change with every build, so the list can't name them.
const SHELL = ["/", "/logo.svg", "/fonts/Inter-latin-Variable.woff2", "/fonts/Inter-latin-ext-Variable.woff2",
  "/fonts/Geist-Variable.woff2", "/manifest.webmanifest"];
// The built files carry their content's hash in their name (and the server sends them `immutable`): a name never
// holds anything else, so a copy kept under it is always right and is used without asking the server.
const ASSETS = "/assets/";
// A build adds a file or two under new names, and the old ones would pile up: keep the most recent this many.
const MAX_ASSETS = 150;
// The pages that are the app itself (main.ts picks the screen from the hash, and /plaid/oauth resumes a bank link).
// Any other page (the OAuth consent screen, Carta's callback) is not the shell, so it never replaces the cached one.
const APP_PAGES = ["/", "/plaid/oauth"];

/** How the worker answers a request: "asset" (a built file: the kept copy first), "shell" (the network first, the
 *  kept copy only when offline) or null (not the worker's: the browser goes to the server as usual). */
function route(request, url, origin) {
  if (request.method !== "GET" || url.origin !== origin) return null;
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/auth/")) return null;
  if (request.mode === "navigate") return APP_PAGES.includes(url.pathname) ? "shell" : null;
  if (url.pathname.startsWith(ASSETS)) return request.headers.has("Range") ? null : "asset";
  return SHELL.includes(url.pathname) ? "shell" : null;
}

/** Whether an answer may be kept as `kind`'s copy. Never a sign-in redirect that was followed (it's the sign-in page,
 *  whatever address was asked for), an opaque or partial answer, or, for a built file, a web page: an address the app
 *  doesn't have gets the app's own page (with a one-time nonce in it), not a 404. */
function keepable(res, kind, request) {
  if (res.status !== 200 || res.redirected || res.type !== "basic") return false;
  const type = res.headers.get("Content-Type") || "";
  if (kind === "asset") return !type.startsWith("text/html");
  // Only the app's own page is kept as the page to open offline (not, say, a link straight to a script).
  return request.mode !== "navigate" || type.startsWith("text/html");
}

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).catch(() => {}).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    for (const key of await caches.keys()) if (key !== CACHE) await caches.delete(key);
    await self.clients.claim();
  })());
});

async function keep(key, res) {
  const cache = await caches.open(CACHE);
  await cache.put(key, res);
  const assets = (await cache.keys()).filter((r) => new URL(r.url).pathname.startsWith(ASSETS));
  for (const old of assets.slice(0, Math.max(0, assets.length - MAX_ASSETS))) await cache.delete(old);   // oldest first
}

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  const kind = route(event.request, url, location.origin);
  if (!kind) return;
  const key = event.request.mode === "navigate" ? "/" : url.pathname;
  if (kind === "asset") {
    // The kept copy first; a file not kept yet comes from the server (and is kept if it is what it should be).
    event.respondWith((async () => {
      const hit = await (await caches.open(CACHE)).match(key);
      if (hit) return hit;
      const res = await fetch(event.request);
      if (keepable(res, kind, event.request)) event.waitUntil(keep(key, res.clone()).catch(() => {}));
      return res;
    })());
    return;
  }
  // Network first for the app itself (so updates show up at once); the kept copy only when offline.
  event.respondWith((async () => {
    try {
      const res = await fetch(event.request);
      if (keepable(res, kind, event.request)) event.waitUntil(keep(key, res.clone()).catch(() => {}));
      return res;
    } catch (err) {
      const cached = await caches.match(key);
      if (cached) return cached;
      throw err;
    }
  })());
});

self.addEventListener("push", (event) => {
  let msg;
  try { msg = event.data ? event.data.json() : {}; } catch { msg = { body: event.data && event.data.text() }; }
  event.waitUntil(self.registration.showNotification(msg.title || "Runway", {
    body: msg.body || "",
    icon: "/icon-192.png",
    badge: "/icon-192.png",
    tag: msg.tag || undefined,
    data: { url: msg.url || "/" },
  }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  let target = new URL(event.notification.data?.url || "/", location.origin);
  if (target.origin !== location.origin) target = new URL("/", location.origin);   // a notification only opens Runway
  target = target.href;
  event.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const w of wins) {
      if (new URL(w.url).origin === location.origin) {
        await w.focus();
        return w.navigate ? w.navigate(target) : undefined;
      }
    }
    return self.clients.openWindow(target);
  })());
});
