// Runway's service worker: shows push notifications, and keeps the app's shell around so it opens without a
// connection (with the last-loaded page). Data always comes fresh from the server; nothing from /api is cached.
const CACHE = "runway-shell-v3";
// The app's page and Runway's own files. The app's built files (/assets/…) are kept as the page loads them: their
// names change with every build, so the list can't name them.
const SHELL = ["/", "/logo.svg", "/fonts/Geist-Variable.woff2", "/manifest.webmanifest"];
const isShell = (path) => SHELL.includes(path) || path.startsWith("/assets/");

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).catch(() => {}).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    for (const key of await caches.keys()) if (key !== CACHE) await caches.delete(key);
    await self.clients.claim();
  })());
});

// Network first for the app itself (so updates show up at once); the cached copy only when offline.
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/auth/")) return;
  const key = event.request.mode === "navigate" ? "/" : url.pathname;
  if (event.request.mode !== "navigate" && !isShell(key)) return;
  event.respondWith((async () => {
    try {
      const res = await fetch(event.request);
      // Only the app's own page is kept as the page to open offline (not, say, a link straight to a script).
      const page = event.request.mode !== "navigate" || (res.headers.get("Content-Type") || "").startsWith("text/html");
      if (res.ok && !res.redirected && page) (await caches.open(CACHE)).put(key, res.clone());
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
