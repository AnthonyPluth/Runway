// What the extension does inside a store's page. Each function is self-contained: it's run in a store tab
// (chrome.scripting) or, from frame.js, in the extension's own hidden frame of the store.

function pageHtml() { return { url: location.href, html: document.documentElement.outerHTML }; }

async function pageFetch(url, init) {
  try {
    if (new URL(url, location.href).protocol !== "https:") throw new Error("not an https address");
    const res = await fetch(url, { credentials: "include", signal: AbortSignal.timeout(45000), ...init });
    return { ok: res.ok, status: res.status, url: res.url, text: await res.text() };
  } catch (e) {
    return { ok: false, status: 0, url, text: "", error: String(e && e.message || e) };
  }
}

function pageGo(url) {
  if (new URL(url, location.href).protocol !== "https:") throw new Error("not an https address");   // never javascript:
  setTimeout(() => { location.href = url; }, 0);
  return true;
}

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

// Costco: costco.com's account page keeps the sign-in it signs its order requests with in localStorage. Only whether
// it's there (and still good) goes back to the extension: the values themselves are read (and used) right here.
// The token lasts fifteen minutes, and the page swaps in a new one as it loads, so one left over from your last visit
// is `stale`: there, but no use until the page has renewed it.
function costcoState(storage) {
  const get = (key) => { try { return localStorage.getItem(key); } catch (_) { return null; } };
  const entries = Object.values(storage || {});
  const present = entries.every(([key]) => !!get(key));
  const expired = entries.some(([key, prefix]) => {
    if (!prefix || !get(key)) return false;   // only the header with a "Bearer " in front is a token that runs out
    try {
      const claims = JSON.parse(atob(get(key).split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
      return typeof claims.exp === "number" && claims.exp * 1000 - Date.now() < 120000;
    } catch (_) { return false; }
  });
  const link = [...document.querySelectorAll('a[href*="ordersandpurchases"]')].map((a) => a.href)[0] || null;
  return { url: location.href, link, ready: present && !expired, stale: present && expired,
           signedOut: /\/(LogonForm|LogoffView)|signin\.costco\.com/i.test(location.href) };
}

// One request to Costco's order service, signed the way costco.com's own page signs it: `storage` says which stored
// value goes in which header. No cookies go with it (the site's own requests carry none: its sign-in is the header).
async function costcoFetch(url, init, storage) {
  try {
    const u = new URL(url, location.href);
    if (u.protocol !== "https:") throw new Error("not an https address");
    const headers = { ...((init && init.headers) || {}) };
    for (const [header, [key, prefix]] of Object.entries(storage || {})) {
      const value = localStorage.getItem(key);
      if (!value) return { ok: false, status: 0, url, text: "", signedOut: true, error: "not signed in" };
      headers[header] = (prefix || "") + value;
    }
    const res = await fetch(u.href, { signal: AbortSignal.timeout(45000), ...init, credentials: "omit", headers });
    return { ok: res.ok, status: res.status, url: res.url, text: await res.text() };
  } catch (e) {
    return { ok: false, status: 0, url, text: "", error: String(e && e.message || e) };
  }
}

// name -> [function, world it needs when run in a tab]. Used by background.js and frame.js, which load after this file.
/* exported PAGE_COMMANDS */
const PAGE_COMMANDS = {
  html: [pageHtml, "ISOLATED"],
  fetch: [pageFetch, "ISOLATED"],
  go: [pageGo, "ISOLATED"],
  discover: [targetDiscover, "MAIN"],
  calls: [targetCalls, "MAIN"],
  look: [cartaLook, "ISOLATED"],
  costcoState: [costcoState, "ISOLATED"],
  costcoFetch: [costcoFetch, "ISOLATED"],
};
