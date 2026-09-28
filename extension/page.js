// What the extension does inside a store's page. Each function is self-contained: it's run in a store tab
// (chrome.scripting) or, from frame.js, in the extension's own hidden frame of the store.

function pageHtml() { return { url: location.href, html: document.documentElement.outerHTML }; }

async function pageFetch(url, init) {
  try {
    const res = await fetch(url, { credentials: "include", signal: AbortSignal.timeout(45000), ...init });
    return { ok: res.ok, status: res.status, url: res.url, text: await res.text() };
  } catch (e) {
    return { ok: false, status: 0, url, text: "", error: String(e && e.message || e) };
  }
}

function pageGo(url) { setTimeout(() => { location.href = url; }, 0); return true; }

// Target's order API and its key, as target.com's own orders page called it (or as its page settings give it).
function targetDiscover(fallbackBase) {
  // its sign-in token too, for its APIs that want it as a header rather than a cookie
  const token = (document.cookie.match(/(?:^|;\s*)accessToken=([^;]+)/) || [])[1] || null;
  const seen = performance.getEntriesByType("resource").map((e) => e.name).filter((u) => u.includes("guest_order_aggregations"));
  for (const u of seen) {
    try {
      const url = new URL(u);
      const at = url.pathname.indexOf("/guest_order_aggregations/");
      const base = url.origin + url.pathname.slice(0, at) + "/guest_order_aggregations/v1";
      const key = url.searchParams.get("key");
      if (key) return { base, key, from: "page", token };
    } catch (_) { /* keep looking */ }
  }
  // Target's other APIs (post_orders and the like) take the same key.
  for (const u of performance.getEntriesByType("resource").map((e) => e.name)) {
    try {
      const url = new URL(u);
      const key = url.hostname === "api.target.com" && url.searchParams.get("key");
      if (key) return { base: fallbackBase, key, from: "page", token };
    } catch (_) { /* keep looking */ }
  }
  const html = document.documentElement.innerHTML;
  const m = html.match(/"apiKey"\s*:\s*"([0-9a-f]{32,64})"/i) || html.match(/[?&]key=([0-9a-f]{32,64})/i);
  return { base: fallbackBase, key: m ? m[1] : null, from: m ? "settings" : null, token, signedIn: !/login|signin/i.test(location.pathname) };
}

// The target.com data addresses this page called that mention an order (its order details page calling for its items).
function targetCalls(order) {
  const seen = performance.getEntriesByType("resource")
    .filter((e) => e.initiatorType === "fetch" || e.initiatorType === "xmlhttprequest")
    .map((e) => e.name)
    .filter((u) => {
      try { return /(^|\.)target\.com$/.test(new URL(u).hostname) && (u.includes(order) || u.includes(encodeURIComponent(order))); }
      catch (_) { return false; }
    });
  return [...new Set(seen)];
}

// Carta: the data addresses the page has used, JSON embedded in the page, and links to other holdings pages.
function cartaLook() {
  const same = (u) => { try { return /(^|\.)carta\.com$/.test(new URL(u, location.href).hostname); } catch (_) { return false; } };
  const used = performance.getEntriesByType("resource")
    .filter((e) => (e.initiatorType === "fetch" || e.initiatorType === "xmlhttprequest") && same(e.name))
    .map((e) => e.name);
  const embedded = [...document.querySelectorAll('script[type="application/json"], script#__NEXT_DATA__')]
    .map((s) => s.textContent).filter((t) => t && t.length < 3000000);
  const links = [...document.querySelectorAll("a[href]")].map((a) => a.href).filter(same);
  return { url: location.href, used: [...new Set(used)], embedded, links: [...new Set(links)],
           signedOut: /\/(login|signin|accounts\/login)/i.test(location.pathname) };
}

// name -> [function, world it needs when run in a tab]
const PAGE_COMMANDS = {
  html: [pageHtml, "ISOLATED"],
  fetch: [pageFetch, "ISOLATED"],
  go: [pageGo, "ISOLATED"],
  discover: [targetDiscover, "MAIN"],
  calls: [targetCalls, "MAIN"],
  look: [cartaLook, "ISOLATED"],
};
