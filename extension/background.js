// Runway orders: reads your Amazon and Target order history with the sign-in already in this browser and sends it to
// your Runway, which does all the reading of those pages. Nothing goes anywhere but the Runway address you set.
//
// Each store is read in a background tab of its own site, so every request is the site's own kind of request (same
// cookies, same origin), and the tab is closed afterwards.

const AMAZON = "https://www.amazon.com";
const AMAZON_TRANSACTIONS = `${AMAZON}/cpe/yourpayments/transactions`;
const AMAZON_ORDER = (n) => `${AMAZON}/gp/your-account/order-details?orderID=${encodeURIComponent(n)}`;
const TARGET_ORDERS = "https://www.target.com/orders";
const TARGET_API = "https://api.target.com/guest_order_aggregations/v1";
const MAX_PAGES = 60;
const PAUSE_MS = 400;        // between order pages, to go at a person's pace rather than hammer the store
const RETAILERS = { amazon: "Amazon", target: "Target", carta: "Carta" };
const EVERYDAY = ["amazon", "target"];   // "Import both"; Carta has its own button (and joins the daily import once it's worked)

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const store = chrome.storage.local;

async function settings() {
  const s = await store.get(["runwayUrl", "token", "auto"]);
  return { runwayUrl: (s.runwayUrl || "").replace(/\/+$/, ""), token: s.token || "", auto: !!s.auto };
}

async function setStatus(patch) {
  const { status = {} } = await store.get("status");
  await store.set({ status: { ...status, ...patch, at: new Date().toISOString() } });
}

async function runway(path, body) {
  const { runwayUrl, token } = await settings();
  if (!runwayUrl || !token) throw new Error("Set your Runway address and key in this extension's options first.");
  let res;
  try {
    res = await fetch(runwayUrl + path, {
      method: "POST",
      headers: { "Authorization": `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
  } catch (e) {
    throw new Error(`Couldn't reach Runway at ${runwayUrl} (${e.message}).`);
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Runway answered ${res.status}.`);
  return data;
}

// ------------------------------------------------------------------------------------------------ tabs

function waitForLoad(tabId, timeoutMs = 45000) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { done(); reject(new Error("The store's page took too long to load.")); }, timeoutMs);
    const listener = (id, info, tab) => { if (id === tabId && info.status === "complete") { done(); resolve(tab); } };
    const done = () => { clearTimeout(timer); chrome.tabs.onUpdated.removeListener(listener); };
    chrome.tabs.onUpdated.addListener(listener);
    chrome.tabs.get(tabId).then((t) => { if (t.status === "complete" && t.url && t.url !== "about:blank") { done(); resolve(t); } }, () => {});
  });
}

async function openTab(url) {
  const tab = await chrome.tabs.create({ url, active: false });
  return waitForLoad(tab.id).then((t) => t, async (e) => { await closeTab(tab.id); throw e; });
}

async function navigate(tabId, url) {
  await chrome.tabs.update(tabId, { url });
  return waitForLoad(tabId);
}

async function closeTab(tabId) {
  try { await chrome.tabs.remove(tabId); } catch (_) { /* already closed */ }
}

async function inPage(tabId, func, args = [], world = "ISOLATED") {
  const [r] = await chrome.scripting.executeScript({ target: { tabId }, func, args, world });
  if (r && r.error) throw new Error(String(r.error.message || r.error));
  return r ? r.result : undefined;
}

// These run inside the store's page.
function pageHtml() { return { url: location.href, html: document.documentElement.outerHTML }; }

async function pageFetch(url, init) {
  try {
    const res = await fetch(url, { credentials: "include", ...init });
    return { ok: res.ok, status: res.status, url: res.url, text: await res.text() };
  } catch (e) {
    return { ok: false, status: 0, url, text: "", error: String(e && e.message || e) };
  }
}

// Target's order API and its key, as target.com's own orders page called it (or as its page settings give it).
function targetDiscover(fallbackBase) {
  const seen = performance.getEntriesByType("resource").map((e) => e.name).filter((u) => u.includes("guest_order_aggregations"));
  for (const u of seen) {
    try {
      const url = new URL(u);
      const at = url.pathname.indexOf("/guest_order_aggregations/");
      const base = url.origin + url.pathname.slice(0, at) + "/guest_order_aggregations/v1";
      const key = url.searchParams.get("key");
      if (key) return { base, key, from: "page" };
    } catch (_) { /* keep looking */ }
  }
  const html = document.documentElement.innerHTML;
  const m = html.match(/"apiKey"\s*:\s*"([0-9a-f]{32,64})"/i) || html.match(/[?&]key=([0-9a-f]{32,64})/i);
  const token = (document.cookie.match(/(?:^|;\s*)accessToken=([^;]+)/) || [])[1] || null;
  return { base: fallbackBase, key: m ? m[1] : null, from: m ? "settings" : null, token, signedIn: !/login|signin/i.test(location.pathname) };
}

// ------------------------------------------------------------------------------------------------ Amazon

async function importAmazon(progress) {
  await runway("/api/ext/start", { retailer: "amazon" });
  progress("Opening your Amazon payments…");
  const tab = await openTab(AMAZON_TRANSACTIONS);
  let keepTab = false;
  try {
    if (/\/ap\/signin|\/ax\/claim/.test(tab.url || "")) {
      keepTab = true;
      await chrome.tabs.update(tab.id, { active: true });
      throw new Error("Sign in to Amazon in the tab that just opened, then import again.");
    }
    let page = await inPage(tab.id, pageHtml);
    const orders = new Set();
    for (let n = 1; n <= MAX_PAGES; n++) {
      progress(`Reading Amazon payments, page ${n}…`);
      const r = await runway("/api/ext/amazon/transactions", { html: page.html });
      r.orders.forEach((o) => orders.add(o));
      if (!r.next_form) break;
      const res = await inPage(tab.id, pageFetch, [AMAZON_TRANSACTIONS, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams(r.next_form).toString(),
      }]);
      if (!res.ok) throw new Error(`Amazon's payments page answered ${res.status}.`);
      page = { html: res.text };
      await sleep(PAUSE_MS);
    }
    let i = 0;
    for (const order of orders) {
      progress(`Reading Amazon order ${++i} of ${orders.size}…`);
      const res = await inPage(tab.id, pageFetch, [AMAZON_ORDER(order)]);
      let r = res.ok ? await runway("/api/ext/amazon/order", { order_number: order, html: res.text }) : { read: false };
      if (!r.read) {   // some pages only fill in once their scripts run: load it for real and read what shows
        await navigate(tab.id, AMAZON_ORDER(order));
        await sleep(1500);
        const shown = await inPage(tab.id, pageHtml);
        r = await runway("/api/ext/amazon/order", { order_number: order, html: shown.html });
      }
      await sleep(PAUSE_MS);
    }
  } finally {
    if (!keepTab) await closeTab(tab.id);
  }
  progress("Matching Amazon orders to your transactions…");
  return runway("/api/ext/finish", { retailer: "amazon" });
}

// ------------------------------------------------------------------------------------------------ Target

async function targetJson(tabId, url, token) {
  const headers = { Accept: "application/json" };
  let res = await inPage(tabId, pageFetch, [url, { headers }]);
  if ((res.status === 401 || res.status === 403) && token) {
    res = await inPage(tabId, pageFetch, [url, { headers: { ...headers, Authorization: `Bearer ${token}` } }]);
  }
  if (res.status === 401 || res.status === 403) throw Object.assign(new Error("signin"), { signin: true });
  if (!res.ok) return null;
  try { return JSON.parse(res.text); } catch (_) { return null; }
}

async function importTarget(progress) {
  const start = await runway("/api/ext/start", { retailer: "target" });
  progress("Opening your Target orders…");
  const tab = await openTab(TARGET_ORDERS);
  let keepTab = false;
  const signIn = async () => {
    keepTab = true;
    await chrome.tabs.update(tab.id, { active: true });
    return new Error("Sign in to Target in the tab that just opened, then import again.");
  };
  try {
    if (/login|signin/i.test(new URL(tab.url || TARGET_ORDERS).pathname)) throw await signIn();
    await sleep(3000);   // let the orders page make its own calls, so we can see how it calls the API
    const api = await inPage(tab.id, targetDiscover, [TARGET_API], "MAIN");
    if (!api.key) {
      if (api.signedIn === false) throw await signIn();
      throw new Error("Couldn't find how target.com reads your orders. Runway may need an update for Target's site.");
    }
    const need = [];
    try {
      for (const type of ["ONLINE", "STORE"]) {
        for (let page = 1; page <= MAX_PAGES; page++) {
          progress(`Reading Target ${type === "STORE" ? "in-store purchases" : "online orders"}, page ${page}…`);
          const url = `${api.base}/order_history?page_number=${page}&page_size=10&order_purchase_type=${type}` +
            `&pending_order=true&shipt_status=true&key=${encodeURIComponent(api.key)}`;
          const data = await targetJson(tab.id, url, api.token);
          if (!data) break;
          const r = await runway("/api/ext/target/history", { purchase_type: type, page, data });
          r.orders.forEach((n) => need.push({ n, type }));
          if (!r.more) break;
          await sleep(PAUSE_MS);
        }
      }
      let i = 0;
      for (const { n, type } of need) {
        progress(`Reading Target ${type === "STORE" ? "receipt" : "order"} ${++i} of ${need.length}…`);
        for (const tpl of (start.detail_urls || {})[type === "STORE" ? "store" : "online"] || []) {
          const url = tpl.replace("{base}", api.base).replace("{key}", encodeURIComponent(api.key)).replace("{order}", encodeURIComponent(n));
          const data = await targetJson(tab.id, url, api.token);
          if (!data) continue;
          const r = await runway("/api/ext/target/order", { order_number: n, data });
          if (r.read) break;
        }
        await sleep(PAUSE_MS);
      }
    } catch (e) {
      if (e.signin) throw await signIn();
      throw e;
    }
  } finally {
    if (!keepTab) await closeTab(tab.id);
  }
  progress("Matching Target orders to your transactions…");
  return runway("/api/ext/finish", { retailer: "target" });
}

// ------------------------------------------------------------------------------------------------ Carta
//
// Carta's pages load your holdings from Carta's own data addresses. The extension notes which ones a page used,
// reads them again (reads only, on carta.com), and sends the replies to Runway, which finds the grants in them.

const CARTA_MAX_PAGES = 12;
const CARTA_PAGE = /portfolio|holding|securit|equity|grant|option|certificate|compan|issuer/i;
const CARTA_NEVER = /logout|log-out|sign-?out|delete|remove|cancel|accept|exercise|consent|download|export|upload|\.pdf/i;

// Runs in the Carta page: the data addresses it has used, JSON embedded in the page, and links to other holdings pages.
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

async function importCarta(progress) {
  const start = await runway("/api/ext/carta/start", {});
  progress("Opening Carta…");
  const tab = await openTab(start.start_url);
  let keepTab = false;
  const seen = new Set(), queue = [], pagesVisited = new Set();
  let sent = 0;
  const readData = async (url) => {
    if (seen.has(url) || CARTA_NEVER.test(url) || sent >= start.max_follow) return;
    seen.add(url);
    const res = await inPage(tab.id, pageFetch, [url, { headers: { Accept: "application/json" } }]);
    if (!res.ok || !res.text || !/^\s*[[{]/.test(res.text)) return;
    let data;
    try { data = JSON.parse(res.text); } catch (_) { return; }
    const r = await runway("/api/ext/carta/data", { url, data });
    sent++;
    (r.follow || []).forEach((u) => queue.push(u));
  };
  try {
    await sleep(4000);   // Carta's pages load their data after they appear
    let look = await inPage(tab.id, cartaLook);
    if (look.signedOut) {
      keepTab = true;
      await chrome.tabs.update(tab.id, { active: true });
      throw new Error("Sign in to Carta in the tab that just opened, then import again.");
    }
    const pages = [look.url];
    for (let p = 0; p < pages.length && p < CARTA_MAX_PAGES; p++) {
      if (p > 0) {
        progress(`Reading Carta page ${p + 1}…`);
        await navigate(tab.id, pages[p]);
        await sleep(3500);
        look = await inPage(tab.id, cartaLook);
      }
      pagesVisited.add(look.url);
      for (const [i, text] of look.embedded.entries()) {
        try { await runway("/api/ext/carta/data", { url: `${look.url}#embedded-${i}`, data: JSON.parse(text) }); sent++; } catch (_) { /* not JSON */ }
      }
      for (const u of look.used) { progress(`Reading Carta (${sent} replies so far)…`); await readData(u); }
      while (queue.length) await readData(queue.shift());
      for (const l of look.links) {
        const clean = l.split("#")[0];
        if (CARTA_PAGE.test(clean) && !CARTA_NEVER.test(clean) && !pagesVisited.has(clean) && !pages.includes(clean)) pages.push(clean);
      }
    }
  } finally {
    if (!keepTab) await closeTab(tab.id);
  }
  progress("Saving your equity in Runway…");
  return runway("/api/ext/carta/finish", {});
}

// ------------------------------------------------------------------------------------------------ running

let running = null;

function summary(r) {
  if (r.grants !== undefined) return r.grants ? `${r.companies} compan${r.companies === 1 ? "y" : "ies"} · ${r.grants} grant${r.grants === 1 ? "" : "s"}`
    : `No grants found in ${r.pages} replies (see Runway's Settings)`;
  const bits = [`${r.orders} orders`];
  if (r.matched) bits.push(`${r.matched} newly matched`);
  if (r.split) bits.push(`${r.split} split`);
  if (r.category) bits.push(`${r.category} categorized`);
  if (r.unmatched) bits.push(`${r.unmatched} not matched yet`);
  return bits.join(" · ");
}

async function run(which) {
  if (running) return running;
  running = (async () => {
    const keepAlive = setInterval(() => chrome.runtime.getPlatformInfo(() => {}), 20000);   // a long import outlives the idle timer
    const { results = {} } = await store.get("results");
    try {
      const everyday = which === "daily" ? [...EVERYDAY, ...(results.carta?.ok ? ["carta"] : [])] : EVERYDAY;
      for (const retailer of which === "all" || which === "daily" ? everyday : [which]) {
        const progress = (message) => setStatus({ running: true, retailer, message });
        try {
          const r = await { amazon: importAmazon, target: importTarget, carta: importCarta }[retailer](progress);
          results[retailer] = { ok: true, at: new Date().toISOString(), message: summary(r) };
        } catch (e) {
          results[retailer] = { ok: false, at: new Date().toISOString(), message: e.message || String(e) };
        }
        await store.set({ results });
      }
    } finally {
      clearInterval(keepAlive);
      await setStatus({ running: false, retailer: null, message: "" });
      running = null;
    }
    return results;
  })();
  return running;
}

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg && msg.type === "import" && (msg.retailer === "all" || RETAILERS[msg.retailer])) {
    run(msg.retailer);
    reply({ started: true });
  }
  if (msg && msg.type === "settings-changed") scheduleAuto().then(() => reply({ ok: true }));
  return true;
});

// Once a day, if you asked for it (Options): the same import, in background tabs.
async function scheduleAuto() {
  const { auto } = await settings();
  await chrome.alarms.clear("daily");
  if (auto) chrome.alarms.create("daily", { delayInMinutes: 5, periodInMinutes: 24 * 60 });
}
chrome.alarms.onAlarm.addListener((a) => { if (a.name === "daily") run("daily"); });
chrome.runtime.onInstalled.addListener((d) => {
  scheduleAuto();
  if (d.reason === "install") chrome.runtime.openOptionsPage();
});
chrome.runtime.onStartup.addListener(scheduleAuto);
setStatus({ running: false, retailer: null, message: "" });
