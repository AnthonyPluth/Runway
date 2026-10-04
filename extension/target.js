// Target: its order history API, with the key its own orders page uses, then each order's details.
/* global sleep, store, runway, HiddenUnavailable, MAX_PAGES, stopsImport -- from the other files here (see background.js) */
/* exported importTarget */

const TARGET_ORDERS = "https://www.target.com/orders";
const TARGET_API = "https://api.target.com/guest_order_aggregations/v1";



// ------------------------------------------------------------------------------------------------ Target

// Target reads slower than the other stores, with a person's unevenness: it's quick to take a burst of reads for a bot.
// After it has said "too many requests" once, the rest of the import goes slower (targetBackoff, up to four times).
let targetBackoff = 1;
const TARGET_RETRY_MS = 10000;   // how long to wait after "too many requests" before asking again
const targetPause = () => sleep((1500 + Math.random() * 2000) * targetBackoff);

// One read of a Target address. "Too many requests" (429) is usually a moment's too many (the next read a second later
// is answered), so it's waited out once, and the import slows down; only a second one in a row is the end of it.
async function targetGet(page, url, headers) {
  let res = await page.run("fetch", url, { headers });
  if (res.status === 429) {
    targetBackoff = Math.min(targetBackoff * 2, 4);
    await sleep(TARGET_RETRY_MS + Math.random() * 5000);
    res = await page.run("fetch", url, { headers });
  }
  return res;
}

// A reply from Target's API as JSON (null if none). A refusal (401/403) means signed out when reading the order
// history; for an order's details it may only mean that address isn't one this account can use, so it's null too.
// "Too many requests" (429) stops the import either way: reading on would only look more like a bot. So does any
// failure reading the history, whose pages can't be skipped.
async function targetJson(page, url, token, { detail = false } = {}) {
  const headers = { Accept: "application/json" };
  let res = await targetGet(page, url, headers);
  // Target's sign-in token only ever goes to Target's own API.
  if ((res.status === 401 || res.status === 403) && token && new URL(url).hostname === "api.target.com") {
    res = await targetGet(page, url, { ...headers, Authorization: `Bearer ${token}` });
  }
  if ((res.status === 401 || res.status === 403) && !detail) throw Object.assign(new Error("signin"), { signin: true });
  if (res.status === 429 || (!detail && !res.ok)) {
    throw Object.assign(new Error(`Target stopped answering (${res.status ? `it answered ${res.status}` : res.error || "no reply"}). ` +
      "Runway kept what it read and will read the rest next time."), { limited: true });
  }
  if (!res.ok) return null;
  try { return JSON.parse(res.text); } catch (_) { return null; }
}

const TARGET_LEARNED = 3;           // addresses kept per kind of order, learned from target.com's own order pages
const TARGET_DISCOVER_MS = 15000;   // how long to watch the orders page for its API key

// Target's API calls as the browser makes them (from our hidden frames or tabs alike): the most recent ones, newest last.
// The page's own list of what it loaded fills up on a busy page like Target's, so the calls are watched here instead.
const targetRequests = [];
if (chrome.webRequest && chrome.webRequest.onBeforeRequest) {
  chrome.webRequest.onBeforeRequest.addListener((d) => {
    targetRequests.push(d.url);
    if (targetRequests.length > 200) targetRequests.splice(0, targetRequests.length - 200);
  }, { urls: ["https://api.target.com/*"] });
}

// The order API and its key, from the calls Target's pages made (its other APIs take the same key).
function targetApiFromRequests() {
  let other = null;
  for (const u of [...targetRequests].reverse()) {
    try {
      const url = new URL(u);
      const key = url.searchParams.get("key");
      if (!key) continue;
      const at = url.pathname.indexOf("/guest_order_aggregations/");
      if (at >= 0) return { base: url.origin + url.pathname.slice(0, at) + "/guest_order_aggregations/v1", key, from: "requests" };
      other = other || { base: TARGET_API, key, from: "requests" };
    } catch (_) { /* keep looking */ }
  }
  return other;
}

// An address for a page of Target's order history from one of Runway's templates ({base} {page} {size} {type} {key}).
function targetHistoryUrl(tpl, api, type, page, size) {
  return tpl.replace("{base}", api.base).replace("{page}", page).replace("{size}", size).replace("{type}", type)
    .replace("{key}", encodeURIComponent(api.key));
}
// What older versions of Runway didn't say: the address the extension always used.
const TARGET_OLD_HISTORY = "{base}/order_history?page_number={page}&page_size={size}&order_purchase_type={type}" +
  "&pending_order=true&shipt_status=true&key={key}";

function targetUrl(tpl, api, order) {
  return tpl.replace("{base}", api.base).replace("{key}", encodeURIComponent(api.key)).replace("{order}", encodeURIComponent(order));
}

// Where to read an order's items: what this browser learned from target.com's own pages first, then Runway's list.
async function targetDetailTemplates(fromRunway) {
  const { targetLearned = {} } = await store.get("targetLearned");
  const out = {};
  for (const kind of ["online", "store"]) out[kind] = [...new Set([...(targetLearned[kind] || []), ...((fromRunway || {})[kind] || [])])];
  return out;
}

// An address target.com's order page used for this order, kept as a template ({order}, {key}) for the next ones.
async function learnTargetDetail(kind, url, api, order) {
  const tpl = url.split(encodeURIComponent(order)).join("{order}").split(order).join("{order}")
    .replace(/([?&]key=)[^&]*/, "$1{key}");
  if (!tpl.includes("{order}")) return;
  const { targetLearned = {} } = await store.get("targetLearned");
  const list = [tpl, ...(targetLearned[kind] || []).filter((t) => t !== tpl)].slice(0, TARGET_LEARNED);
  await store.set({ targetLearned: { ...targetLearned, [kind]: list } });
}

// Reads one address for an order's details and sends it to Runway. True when Runway found the order's items in it.
// Not the order's last try in this import, so it doesn't count towards giving up on it.
async function targetDetail(page, api, order, url) {
  const data = await targetJson(page, url, api.token, { detail: true });
  if (!data) return false;
  return (await runway("/api/ext/target/order", { order_number: order, data, final: false })).read;
}

// The order couldn't be read in this import: one try used, so it isn't asked for forever.
const targetGaveUp = (order) => runway("/api/ext/target/order", { order_number: order, data: {}, final: true });

// The target.com addresses an order's page called for that order (waiting a little for it to make them).
async function targetCallsFor(page, order) {
  let urls = [];
  for (let waited = 0; waited < 8000; waited += 1000) {
    await sleep(1000);
    const seen = targetRequests.filter((u) => u.includes(order) || u.includes(encodeURIComponent(order)));
    urls = [...new Set([...seen, ...await page.run("calls", order)])];
    if (urls.length && waited >= 2000) break;
  }
  return urls.slice(0, 8);
}

async function importTarget(progress, Page) {
  const start = await runway("/api/ext/start", { retailer: "target" });
  targetBackoff = 1;
  progress("Opening your Target orders…");
  const page = await Page.open(TARGET_ORDERS);
  let complete = true;   // every page of history Runway asked for was read
  try {
    if (/login|signin/i.test(new URL(page.url || TARGET_ORDERS).pathname)) throw await page.signIn("Target");
    // Let the orders page make its own calls, so we can see how it calls the API (it can take a while to start).
    let api = {};
    for (let waited = 0; waited < TARGET_DISCOVER_MS && !api.key; waited += 1000) {
      await sleep(1000);
      api = await page.run("discover", TARGET_API);
      if (!api.key) api = { ...api, ...(targetApiFromRequests() || {}) };
      if (api.signedIn === false) break;
    }
    const { targetApi } = await store.get("targetApi");
    if (!api.key && api.signedIn !== false && targetApi && targetApi.key) {   // the key from the last import that found it
      api = { ...api, base: targetApi.base || TARGET_API, key: targetApi.key, from: "saved" };
    }
    if (api.key && api.from !== "saved") await store.set({ targetApi: { base: api.base, key: api.key } });
    if (!api.key) {
      if (api.signedIn === false) throw await page.signIn("Target");
      if (page.hidden) throw new HiddenUnavailable("no order API key in the hidden page");
      const paths = [...new Set(targetRequests.map((u) => { try { return new URL(u).pathname; } catch (_) { return ""; } }))].filter(Boolean);
      throw new Error("Couldn't find how target.com reads your orders. Runway may need an update for Target's site. " +
        (paths.length ? `(Target's page called: ${paths.slice(-4).join(", ")}, none with a key.)` : "(Target's page made no API calls that Runway could see.)"));
    }
    const need = [];
    let refreshed = false;
    try {
      const hist = start.history || {};
      for (const type of ["ONLINE", "STORE"]) {
        let whole = false;
        // The newer address (100 orders a page) first; if it isn't answered on the first page, the older one (10 a page).
        let source = hist.url ? "new" : "old";
        for (let n = 1; n <= MAX_PAGES; n++) {
          progress(`Reading Target ${type === "STORE" ? "in-store purchases" : "online orders"}, page ${n}…`);
          let data = null;
          if (source === "new") {
            data = await targetJson(page, targetHistoryUrl(hist.url, api, type, n, hist.page_size || 100), api.token, { detail: true });
            if (!data && n === 1) source = "old";
          }
          if (source === "old") {
            const url = targetHistoryUrl(hist.fallback_url || TARGET_OLD_HISTORY, api, type, n, hist.fallback_page_size || 10);
            try {
              data = await targetJson(page, url, api.token);
            } catch (e) {
              // A refusal while you're still signed in is usually a stale token: Target's page renews it when it loads
              // (or when you click on it), so load the orders page again, take the new token and try once more.
              if (!e.signin || refreshed) throw e;
              refreshed = true;
              progress("Target's sign-in looked stale; refreshing it…");
              await page.navigate(TARGET_ORDERS);
              await sleep(3000);
              const fresh = await page.run("discover", TARGET_API);
              if (fresh.signedIn === false) throw e;
              api = { ...api, token: fresh.token || api.token, key: fresh.key || api.key };
              n--; continue;   // read this page again
            }
          }
          if (!data) break;   // not JSON: this page of history couldn't be read
          const r = await runway("/api/ext/target/history", { purchase_type: type, page: n, data });
          r.orders.forEach((o) => need.push({ n: o, type }));
          if (!r.more) { whole = true; break; }
          await targetPause();
        }
        if (!whole) complete = false;   // the next import reads this stretch again
      }
      // Each order's items: first from the addresses Runway names (and any this browser learned from target.com
      // before), then, for orders those don't cover, by loading the order's own page and re-reading what it called.
      const templates = await targetDetailTemplates(start.detail_urls);
      const unread = [];
      let i = 0;
      for (const order of need) {
        progress(`Reading Target ${order.type === "STORE" ? "receipt" : "order"} ${++i} of ${need.length}…`);
        let read = false;
        try {
          for (const tpl of templates[order.type === "STORE" ? "store" : "online"]) {
            if (await targetDetail(page, api, order.n, targetUrl(tpl, api, order.n))) { read = true; break; }
            await targetPause();
          }
        } catch (e) {
          if (stopsImport(e)) throw e;
          console.warn(`Runway: Target order ${order.n}: ${e.message}`);
        }
        if (!read) unread.push(order);
        await targetPause();
      }
      i = 0;
      for (const { n, type } of unread) {
        const kind = type === "STORE" ? "store" : "online";
        const tpl = (start.order_pages || {})[kind];
        progress(`Loading Target ${type === "STORE" ? "receipt" : "order"} ${++i} of ${unread.length}…`);
        let read = false;
        try {
          for (const learned of (await targetDetailTemplates(null))[kind]) {   // one learned from an earlier order here
            if (!templates[kind].includes(learned) && await targetDetail(page, api, n, targetUrl(learned, api, n))) { read = true; break; }
          }
          if (!read && tpl) {
            await page.navigate(tpl.replace("{order}", encodeURIComponent(n)));
            for (const url of await targetCallsFor(page, n)) {
              if (await targetDetail(page, api, n, url)) { read = true; await learnTargetDetail(kind, url, api, n); break; }
            }
          }
        } catch (e) {   // one order that won't load doesn't stop the rest
          if (stopsImport(e)) throw e;
          console.warn(`Runway: Target order ${n}: ${e.message}`);
        }
        if (!read) await targetGaveUp(n);
        await targetPause();
      }
    } catch (e) {
      if (e.signin) throw await page.signIn("Target");
      if (e.limited) {   // keep what was read (matched now), without moving the last import's date on
        progress("Matching Target orders to your transactions…");
        await runway("/api/ext/finish", { retailer: "target", complete: false }).catch(() => {});
      }
      throw e;
    }
  } finally {
    await page.close();
  }
  progress("Matching Target orders to your transactions…");
  return runway("/api/ext/finish", { retailer: "target", complete });
}
