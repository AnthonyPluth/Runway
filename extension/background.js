// Runway orders: reads your Amazon and Target order history with the sign-in already in this browser and sends it to
// your Runway, which does all the reading of those pages. Nothing goes anywhere but the Runway address you set.
//
// Each store is read in a hidden page of its own site (see "pages" below), so nothing opens while it works.

if (typeof importScripts === "function") importScripts("page.js");   // Chrome; Firefox loads it from the manifest

const AMAZON = "https://www.amazon.com";
const AMAZON_TRANSACTIONS = `${AMAZON}/cpe/yourpayments/transactions`;
const AMAZON_ORDER = (n) => `${AMAZON}/gp/your-account/order-details?orderID=${encodeURIComponent(n)}`;
const TARGET_ORDERS = "https://www.target.com/orders";
const TARGET_API = "https://api.target.com/guest_order_aggregations/v1";
const MAX_PAGES = 60;
const PAUSE_MS = 400;        // between order pages, to go at a person's pace rather than hammer the store
const RETAILERS = { amazon: "Amazon", target: "Target", carta: "Carta" };
const EVERYDAY = ["amazon", "target"];   // "Import both"; Carta has its own button (and joins the daily import once it's worked)

const AMAZON_PARALLEL = 4;   // order pages read at once: quicker, and still a light load on Amazon

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Runs fn over items, `width` at a time; the first failure stops it.
async function inParallel(items, width, fn) {
  let next = 0, failed = false;
  await Promise.all(Array.from({ length: Math.min(width, items.length) }, async () => {
    try {
      while (next < items.length && !failed) await fn(items[next++]);
    } catch (e) {
      failed = true;
      throw e;
    }
  }));
}
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

// ------------------------------------------------------------------------------------------------ pages
//
// A store is read in a page of its own site, so every request is the site's own kind of request (same cookies, same
// origin). That page is a hidden frame (in Chrome's offscreen document, or Firefox's background page), so no tab or
// window opens. If a store won't load there, or looks signed out there, it's read in a background tab instead, and
// that store keeps to tabs for a week (or until the extension is updated).

const HIDDEN_SUPPORTED = typeof document !== "undefined" || !!(chrome.offscreen && chrome.offscreen.createDocument);
const HIDDEN_LOAD_MS = 30000;
const HIDDEN_RETRY_MS = 7 * 24 * 3600 * 1000;
const HIDDEN_RULE = 7101;
const STORE_HOSTS = ["amazon.com", "target.com", "carta.com"];
const STORE_MATCHES = ["https://www.amazon.com/*", "https://www.target.com/*", "https://*.carta.com/*"];

class HiddenUnavailable extends Error {}

function waitForLoad(tabId, timeoutMs = 45000) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { done(); reject(new Error("The store's page took too long to load.")); }, timeoutMs);
    const listener = (id, info, tab) => { if (id === tabId && info.status === "complete") { done(); resolve(tab); } };
    const done = () => { clearTimeout(timer); chrome.tabs.onUpdated.removeListener(listener); };
    chrome.tabs.onUpdated.addListener(listener);
    chrome.tabs.get(tabId).then((t) => { if (t.status === "complete" && t.url && t.url !== "about:blank") { done(); resolve(t); } }, () => {});
  });
}

async function closeTab(tabId) {
  try { await chrome.tabs.remove(tabId); } catch (_) { /* already closed */ }
}

async function inPage(tabId, func, args = [], world = "ISOLATED") {
  const [r] = await chrome.scripting.executeScript({ target: { tabId }, func, args, world });
  if (r && r.error) throw new Error(String(r.error.message || r.error));
  return r ? r.result : undefined;
}

// A background tab (not focused), closed afterwards unless you need to sign in there.
class TabPage {
  static async open(url) {
    const tab = await chrome.tabs.create({ url, active: false });
    const loaded = await waitForLoad(tab.id).catch(async (e) => { await closeTab(tab.id); throw e; });
    return new TabPage(loaded);
  }
  constructor(tab) { this.tab = tab; this.hidden = false; this.keep = false; }
  get url() { return this.tab.url || ""; }
  run(cmd, ...args) { const [func, world] = PAGE_COMMANDS[cmd]; return inPage(this.tab.id, func, args, world); }
  async navigate(url) { await chrome.tabs.update(this.tab.id, { url }); this.tab = await waitForLoad(this.tab.id); }
  async signIn(site) {
    this.keep = true;
    await chrome.tabs.update(this.tab.id, { active: true });
    return new Error(`Sign in to ${site} in the tab that just opened, then import again.`);
  }
  async close() { if (!this.keep) await closeTab(this.tab.id); }
}

// Where hidden frames live: Firefox's background page, or Chrome's offscreen document.
const frameHost = typeof document !== "undefined" ? {
  async open(name, url) {
    const f = document.createElement("iframe");
    Object.assign(f, { name, id: name, src: url, width: 1280, height: 900 });
    document.body.append(f);
  },
  async close(name) { document.getElementById(name)?.remove(); },
  async done() {},
} : {
  async open(name, url) {
    try {
      await chrome.offscreen.createDocument({ url: "offscreen.html", reasons: ["DOM_SCRAPING"],
        justification: "Reads your Amazon, Target and Carta pages, with your sign-in, without opening a tab." });
    } catch (e) {
      if (!/single offscreen|already/i.test(String(e && e.message))) throw new HiddenUnavailable(`offscreen: ${e.message}`);
    }
    for (let i = 0; ; i++) {
      try { return await chrome.runtime.sendMessage({ to: "offscreen", cmd: "open", name, url }); } catch (e) {
        if (i >= 10) throw new HiddenUnavailable(`offscreen: ${e.message}`);
        await sleep(100);
      }
    }
  },
  async close(name) { await chrome.runtime.sendMessage({ to: "offscreen", cmd: "close", name }).catch(() => {}); },
  async done() { await chrome.offscreen.closeDocument().catch(() => {}); },
};

const frames = new Map();   // frame name -> HiddenPage
chrome.runtime.onConnect.addListener((port) => {
  const page = frames.get(port.name);
  if (!page || (port.sender && port.sender.id !== chrome.runtime.id)) { port.disconnect(); return; }
  page.attach(port);
});

// A store page in a hidden frame. frame.js in it answers over a port, a new one each time the frame loads a page.
class HiddenPage {
  static async open(url) {
    const page = new HiddenPage(`runway-hidden-${crypto.randomUUID()}`);
    frames.set(page.name, page);
    const ready = page.loaded();
    try {
      await frameHost.open(page.name, url);
      await ready;
    } catch (e) {
      await page.close();
      throw e instanceof HiddenUnavailable ? e : new HiddenUnavailable(`${url} didn't load in a hidden frame (${e.message})`);
    }
    return page;
  }
  constructor(name) { Object.assign(this, { name, hidden: true, port: null, url: "", waiting: [], pending: new Map(), next: 0 }); }
  attach(port) {
    this.port = port;
    this.url = (port.sender && port.sender.url) || this.url;
    port.onMessage.addListener((m) => {
      const p = this.pending.get(m.id);
      if (!p) return;
      this.pending.delete(m.id);
      m.error ? p.reject(new Error(m.error)) : p.resolve(m.result);
    });
    port.onDisconnect.addListener(() => {
      if (this.port !== port) return;
      this.port = null;
      for (const p of this.pending.values()) p.reject(Object.assign(new Error("The page moved on."), { moved: true }));
      this.pending.clear();
    });
    this.waiting.splice(0).forEach((w) => w());
  }
  loaded(ms = HIDDEN_LOAD_MS) {   // the next page to load in the frame
    return new Promise((resolve, reject) => {
      const ok = () => { clearTimeout(timer); resolve(); };
      const timer = setTimeout(() => { this.waiting = this.waiting.filter((w) => w !== ok); reject(new Error("timed out")); }, ms);
      this.waiting.push(ok);
    });
  }
  async run(cmd, ...args) {
    for (let attempt = 0; ; attempt++) {
      if (!this.port) await this.loaded().catch(() => { throw new HiddenUnavailable("the hidden page stopped answering"); });
      try {
        return await new Promise((resolve, reject) => {
          const id = ++this.next;
          this.pending.set(id, { resolve, reject });
          this.port.postMessage({ id, cmd, args });
        });
      } catch (e) {
        if (!e.moved || attempt >= 2) throw e;   // the page went elsewhere (a redirect): ask the new one
      }
    }
  }
  async navigate(url) {
    const ready = this.loaded();
    await this.run("go", url);
    await ready.catch(() => { throw new HiddenUnavailable(`${url} didn't load in a hidden frame`); });
  }
  async signIn() { return new HiddenUnavailable("looked signed out"); }   // perhaps only in a frame: a tab will tell
  async close() {
    frames.delete(this.name);
    try { if (this.port) this.port.disconnect(); } catch (_) { /* gone */ }
    await frameHost.close(this.name);
  }
}

// While hidden frames are in use: let the stores' pages load in a frame (their "don't show me in a frame" headers are
// dropped, but only for frames that aren't in any tab, i.e. the extension's own), and put frame.js in those frames.
async function hiddenFramesOn() {
  await chrome.declarativeNetRequest.updateSessionRules({
    removeRuleIds: [HIDDEN_RULE],
    addRules: [{
      id: HIDDEN_RULE, priority: 1,
      action: { type: "modifyHeaders",
                responseHeaders: ["x-frame-options", "content-security-policy"].map((header) => ({ header, operation: "remove" })) },
      condition: { requestDomains: STORE_HOSTS, resourceTypes: ["sub_frame"], tabIds: [chrome.tabs.TAB_ID_NONE] },
    }],
  });
  const have = await chrome.scripting.getRegisteredContentScripts({ ids: ["runway-hidden"] }).catch(() => []);
  if (!have.length) {
    await chrome.scripting.registerContentScripts([{ id: "runway-hidden", matches: STORE_MATCHES, js: ["page.js", "frame.js"],
      allFrames: true, runAt: "document_idle", persistAcrossSessions: false }]);
  }
}

async function hiddenFramesOff() {
  await chrome.declarativeNetRequest.updateSessionRules({ removeRuleIds: [HIDDEN_RULE] }).catch(() => {});
  await chrome.scripting.unregisterContentScripts({ ids: ["runway-hidden"] }).catch(() => {});
  await frameHost.done();
}

// One store's import: in hidden frames if they work for it in this browser, otherwise in a background tab.
async function importStore(retailer, importer, progress) {
  const { hiddenOff = {} } = await store.get("hiddenOff");
  let why = null;
  if (HIDDEN_SUPPORTED && !(hiddenOff[retailer] > Date.now() - HIDDEN_RETRY_MS)) {
    try {
      try {
        await hiddenFramesOn();
      } catch (e) {
        throw new HiddenUnavailable(`couldn't set up hidden frames (${e.message})`);
      }
      return await importer(progress, HiddenPage);
    } catch (e) {
      if (!(e instanceof HiddenUnavailable)) throw e;
      why = e.message;
    } finally {
      await hiddenFramesOff();
    }
  }
  const r = await importer(progress, TabPage);
  if (why) {   // a tab worked where a hidden frame didn't: keep to tabs for this store for a while
    console.info(`Runway: reading ${retailer} in a tab (in a hidden frame: ${why})`);
    await store.set({ hiddenOff: { ...hiddenOff, [retailer]: Date.now() } });
  }
  return r;
}

// ------------------------------------------------------------------------------------------------ Amazon

async function importAmazon(progress, Page) {
  await runway("/api/ext/start", { retailer: "amazon" });
  progress("Opening your Amazon payments…");
  const page = await Page.open(AMAZON_TRANSACTIONS);
  try {
    if (/\/ap\/signin|\/ax\/claim/.test(page.url)) throw await page.signIn("Amazon");
    let html = (await page.run("html")).html;
    const orders = new Set();
    for (let n = 1; n <= MAX_PAGES; n++) {
      progress(`Reading Amazon payments, page ${n}…`);
      const r = await runway("/api/ext/amazon/transactions", { html });
      r.orders.forEach((o) => orders.add(o));
      if (!r.next_form) break;
      const res = await page.run("fetch", AMAZON_TRANSACTIONS, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams(r.next_form).toString(),
      });
      if (!res.ok) throw new Error(`Amazon's payments page answered ${res.status}.`);
      html = res.text;
      await sleep(PAUSE_MS);
    }
    let done = 0;
    const unread = [];
    await inParallel([...orders], AMAZON_PARALLEL, async (order) => {
      const res = await page.run("fetch", AMAZON_ORDER(order));
      const r = res.ok ? await runway("/api/ext/amazon/order", { order_number: order, html: res.text }) : { read: false };
      if (!r.read) unread.push(order);
      progress(`Reading Amazon orders, ${++done} of ${orders.size}…`);
      await sleep(PAUSE_MS);
    });
    let i = 0;
    for (const order of unread) {   // some pages only fill in once their scripts run: load each for real and read what shows
      progress(`Loading Amazon order ${++i} of ${unread.length}…`);
      await page.navigate(AMAZON_ORDER(order));
      await sleep(1500);
      const shown = await page.run("html");
      await runway("/api/ext/amazon/order", { order_number: order, html: shown.html });
      await sleep(PAUSE_MS);
    }
  } finally {
    await page.close();
  }
  progress("Matching Amazon orders to your transactions…");
  return runway("/api/ext/finish", { retailer: "amazon" });
}

// ------------------------------------------------------------------------------------------------ Target

// Target reads slower than the other stores, with a person's unevenness: it's quick to take a burst of reads for a bot.
const targetPause = () => sleep(1500 + Math.random() * 2000);

// A reply from Target's API as JSON (null if none). A refusal (401/403) means signed out when reading the order
// history; for an order's details it may only mean that address isn't one this account can use, so it's null too.
async function targetJson(page, url, token, { detail = false } = {}) {
  const headers = { Accept: "application/json" };
  let res = await page.run("fetch", url, { headers });
  if ((res.status === 401 || res.status === 403) && token) {
    res = await page.run("fetch", url, { headers: { ...headers, Authorization: `Bearer ${token}` } });
  }
  if ((res.status === 401 || res.status === 403) && !detail) throw Object.assign(new Error("signin"), { signin: true });
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
async function targetDetail(page, api, order, url) {
  const data = await targetJson(page, url, api.token, { detail: true });
  if (!data) return false;
  return (await runway("/api/ext/target/order", { order_number: order, data })).read;
}

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
  progress("Opening your Target orders…");
  const page = await Page.open(TARGET_ORDERS);
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
    try {
      for (const type of ["ONLINE", "STORE"]) {
        for (let n = 1; n <= MAX_PAGES; n++) {
          progress(`Reading Target ${type === "STORE" ? "in-store purchases" : "online orders"}, page ${n}…`);
          const url = `${api.base}/order_history?page_number=${n}&page_size=10&order_purchase_type=${type}` +
            `&pending_order=true&shipt_status=true&key=${encodeURIComponent(api.key)}`;
          const data = await targetJson(page, url, api.token);
          if (!data) break;
          const r = await runway("/api/ext/target/history", { purchase_type: type, page: n, data });
          r.orders.forEach((o) => need.push({ n: o, type }));
          if (!r.more) break;
          await targetPause();
        }
      }
      // Each order's items: first from the addresses Runway names (and any this browser learned from target.com
      // before), then, for orders those don't cover, by loading the order's own page and re-reading what it called.
      const templates = await targetDetailTemplates(start.detail_urls);
      const unread = [];
      let i = 0;
      for (const order of need) {
        progress(`Reading Target ${order.type === "STORE" ? "receipt" : "order"} ${++i} of ${need.length}…`);
        let read = false;
        for (const tpl of templates[order.type === "STORE" ? "store" : "online"]) {
          if (await targetDetail(page, api, order.n, targetUrl(tpl, api, order.n))) { read = true; break; }
          await targetPause();
        }
        if (!read) unread.push(order);
        await targetPause();
      }
      i = 0;
      for (const { n, type } of unread) {
        const kind = type === "STORE" ? "store" : "online";
        const tpl = (start.order_pages || {})[kind];
        if (!tpl) continue;
        progress(`Loading Target ${type === "STORE" ? "receipt" : "order"} ${++i} of ${unread.length}…`);
        let read = false;
        for (const learned of (await targetDetailTemplates(null))[kind]) {   // one learned from an earlier order here
          if (!templates[kind].includes(learned) && await targetDetail(page, api, n, targetUrl(learned, api, n))) { read = true; break; }
        }
        if (!read) {
          await page.navigate(tpl.replace("{order}", encodeURIComponent(n)));
          for (const url of await targetCallsFor(page, n)) {
            if (await targetDetail(page, api, n, url)) { read = true; await learnTargetDetail(kind, url, api, n); break; }
          }
        }
        if (!read) await runway("/api/ext/target/order", { order_number: n, data: {} });   // counts as a try, so it's not reloaded forever
        await targetPause();
      }
    } catch (e) {
      if (e.signin) throw await page.signIn("Target");
      throw e;
    }
  } finally {
    await page.close();
  }
  progress("Matching Target orders to your transactions…");
  return runway("/api/ext/finish", { retailer: "target" });
}

// ------------------------------------------------------------------------------------------------ Carta
//
// Carta's pages load your holdings from Carta's own data addresses. The extension notes which ones a page used,
// reads them again (reads only, on carta.com), and sends the replies to Runway, which finds the grants in them (and
// names more addresses worth reading).

const CARTA_MAX_PAGES = 12;
const CARTA_PAGE = /portfolio|holding|securit|equity|grant|option|certificate|compan|issuer/i;
const CARTA_NEVER = /logout|log-out|sign-?out|delete|remove|cancel|accept|exercise|consent|download|export|upload|\.pdf/i;

async function importCarta(progress, Page) {
  const start = await runway("/api/ext/carta/start", {});
  progress("Opening Carta…");
  const page = await Page.open(start.start_url);
  const seen = new Set(), queue = [], pagesVisited = new Set();
  let sent = 0;
  const readData = async (url) => {
    if (seen.has(url) || CARTA_NEVER.test(url) || sent >= start.max_follow) return;
    seen.add(url);
    const res = await page.run("fetch", url, { headers: { Accept: "application/json" } });
    if (!res.ok || !res.text || !/^\s*[[{]/.test(res.text)) return;
    let data;
    try { data = JSON.parse(res.text); } catch (_) { return; }
    const r = await runway("/api/ext/carta/data", { url, data });
    sent++;
    (r.follow || []).forEach((u) => queue.push(u));
  };
  try {
    await sleep(4000);   // Carta's pages load their data after they appear
    let look = await page.run("look");
    if (look.signedOut) throw await page.signIn("Carta");
    const pages = [look.url];
    for (let p = 0; p < pages.length && p < CARTA_MAX_PAGES; p++) {
      if (p > 0) {
        progress(`Reading Carta page ${p + 1}…`);
        await page.navigate(pages[p]);
        await sleep(3500);
        look = await page.run("look");
      }
      pagesVisited.add(look.url);
      for (const [i, text] of look.embedded.entries()) {
        try {
          const r = await runway("/api/ext/carta/data", { url: `${look.url}#embedded-${i}`, data: JSON.parse(text) });
          sent++;
          (r.follow || []).forEach((u) => queue.push(u));
        } catch (_) { /* not JSON */ }
      }
      for (const u of look.used) { progress(`Reading Carta (${sent} replies so far)…`); await readData(u); }
      while (queue.length) await readData(queue.shift());
      for (const l of look.links) {
        const clean = l.split("#")[0];
        if (CARTA_PAGE.test(clean) && !CARTA_NEVER.test(clean) && !pagesVisited.has(clean) && !pages.includes(clean)) pages.push(clean);
      }
    }
  } finally {
    await page.close();
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
          const r = await importStore(retailer, { amazon: importAmazon, target: importTarget, carta: importCarta }[retailer], progress);
          results[retailer] = { ok: true, at: new Date().toISOString(), message: summary(r), data: r };
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

// When the popup opens: how many charges are still unmatched now, since you may have matched some in Runway yourself.
async function refreshUnmatched() {
  if (running) return;
  const { unmatched } = await runway("/api/ext/status", {});
  const { results = {} } = await store.get("results");
  let changed = false;
  for (const [retailer, n] of Object.entries(unmatched || {})) {
    const r = results[retailer];
    if (!r || !r.ok || !r.data || r.data.unmatched === n) continue;
    r.data.unmatched = n;
    r.message = summary(r.data);
    changed = true;
  }
  if (changed) await store.set({ results });
}

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg && msg.type === "refresh") refreshUnmatched().catch(() => {}).finally(() => reply({ ok: true }));
  if (msg && msg.type === "import" && (msg.retailer === "all" || RETAILERS[msg.retailer])) {
    run(msg.retailer);
    reply({ started: true });
  }
  if (msg && msg.type === "settings-changed") scheduleAuto().then(() => reply({ ok: true }));
  return true;
});

// Once a day, if you asked for it (Options): the same import, out of sight.
async function scheduleAuto() {
  const { auto } = await settings();
  await chrome.alarms.clear("daily");
  if (auto) chrome.alarms.create("daily", { delayInMinutes: 5, periodInMinutes: 24 * 60 });
}
chrome.alarms.onAlarm.addListener((a) => { if (a.name === "daily") run("daily"); });
chrome.runtime.onInstalled.addListener((d) => {
  scheduleAuto();
  store.remove("hiddenOff");   // a new version: try hidden frames again for every store
  if (d.reason === "install") chrome.runtime.openOptionsPage();
});
chrome.runtime.onStartup.addListener(scheduleAuto);
setStatus({ running: false, retailer: null, message: "" });
