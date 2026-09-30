// Runway orders: reads your Amazon, Target and Costco order history with the sign-in already in this browser and sends it to
// your Runway, which does all the reading of those pages. Nothing goes anywhere but the Runway address you set.
//
// Each store is read in a hidden page of its own site (see "pages" below), so nothing opens while it works.

if (typeof importScripts === "function") importScripts("page.js");   // Chrome; Firefox loads it from the manifest
/* global PAGE_COMMANDS -- from page.js, loaded just above */

const AMAZON = "https://www.amazon.com";
const AMAZON_TRANSACTIONS = `${AMAZON}/cpe/yourpayments/transactions`;
const AMAZON_ORDER = (n) => `${AMAZON}/gp/your-account/order-details?orderID=${encodeURIComponent(n)}`;
const TARGET_ORDERS = "https://www.target.com/orders";
const TARGET_API = "https://api.target.com/guest_order_aggregations/v1";
const MAX_PAGES = 60;
const PAUSE_MS = 400;        // between order pages, to go at a person's pace rather than hammer the store
const RETAILERS = { amazon: "Amazon", target: "Target", costco: "Costco", carta: "Carta" };
const EVERYDAY = ["amazon", "target"];   // "Import all"; Costco and Carta have their own buttons (and join once they've worked)
const JOINS_ONCE_WORKED = ["costco", "carta"];   // in the daily import; Costco in "Import all" too (Carta stays out of it)

const AMAZON_PARALLEL = 4;   // order pages read at once: quicker, and still a light load on Amazon

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const PAGE_CALL_MS = 90000;     // longest a store page may take to answer the extension (its own fetches stop at 45s)
const RUNWAY_CALL_MS = 120000;  // and Runway

// The promise, or an error once `ms` have passed without it settling (so a stalled page can't hold an import forever).
function withTimeout(promise, ms, message, ErrorType = Error) {
  let timer;
  const late = new Promise((_, reject) => { timer = setTimeout(() => reject(new ErrorType(message)), ms); });
  return Promise.race([promise, late]).finally(() => clearTimeout(timer));
}

// Errors that end a store's import rather than skipping one order: signed out, a robot check, the store refusing
// more reads for now, or the hidden page giving out (the import starts again in a tab).
const stopsImport = (e) => e instanceof HiddenUnavailable || !!(e && (e.signin || e.limited || e.code === "signin" || e.code === "robot"));

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

// How often the daily import reads Carta, in days (Options): 1 = every daily import, 7 = weekly, 30 = monthly. Carta signs you
// out often, so reading it less often means fewer times you need to sign in again.
const cartaDays = (v) => ([1, 7, 30].includes(Number(v)) ? Number(v) : 1);
async function settings() {
  const s = await store.get(["runwayUrl", "token", "auto", "cartaEvery"]);
  return { runwayUrl: (s.runwayUrl || "").replace(/\/+$/, ""), token: s.token || "", auto: !!s.auto, cartaEvery: cartaDays(s.cartaEvery) };
}

// One write at a time, in order, so a late progress message can't land after "done" and leave the popup "running".
let statusChain = Promise.resolve();
function setStatus(patch) {
  statusChain = statusChain.then(async () => {
    const { status = {} } = await store.get("status");
    await store.set({ status: { ...status, ...patch, at: new Date().toISOString() } });
  }).catch(() => {});
  return statusChain;
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
      signal: AbortSignal.timeout(RUNWAY_CALL_MS),
      redirect: "error",   // the key and the body only ever go to the address that was saved, never wherever it points
    });
  } catch (e) {
    throw new Error(`Couldn't reach Runway at ${runwayUrl} (${e.message}).`, { cause: e });
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(data.error || `Runway answered ${res.status}.`), { code: data.code });
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
const STORE_HOSTS = ["amazon.com", "target.com", "carta.com", "costco.com"];
const STORE_MATCHES = ["https://www.amazon.com/*", "https://www.target.com/*", "https://*.carta.com/*", "https://www.costco.com/*"];

// The only places a store page may be sent or fetch from, whatever an address came from (Runway's replies, a store's
// own pages): a Runway that isn't yours, or someone pretending to be it, can't point the extension at another site
// with your store sign-in, or at a javascript: address.
function storeUrl(url) {
  let u;
  try { u = new URL(url); } catch (_) { u = null; }
  const host = u && u.hostname;
  if (!u || u.protocol !== "https:" || u.username || u.password ||
      !(["www.amazon.com", "www.target.com", "api.target.com", "www.costco.com", "ecom-api.costco.com", "carta.com"].includes(host) ||
      host.endsWith(".carta.com"))) {
    throw new Error(`Runway won't read ${String(url).slice(0, 80)}: it isn't an Amazon, Target, Costco or Carta address.`);
  }
  return u.href;
}
// A page command that goes somewhere (fetch, go) is only ever sent to a store address.
const checkedArgs = (cmd, args) => (cmd === "fetch" || cmd === "go" || cmd === "costcoFetch" ? [storeUrl(args[0]), ...args.slice(1)] : args);

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
  const [r] = await withTimeout(chrome.scripting.executeScript({ target: { tabId }, func, args, world }), PAGE_CALL_MS,
    "The store's page stopped answering.");
  if (r && r.error) throw new Error(String(r.error.message || r.error));
  return r ? r.result : undefined;
}

// A background tab (not focused), closed afterwards unless you need to sign in there.
class TabPage {
  static async open(url) {
    const tab = await chrome.tabs.create({ url: storeUrl(url), active: false });
    const loaded = await waitForLoad(tab.id).catch(async (e) => { await closeTab(tab.id); throw e; });
    return new TabPage(loaded);
  }
  constructor(tab) { this.tab = tab; this.hidden = false; this.keep = false; }
  get url() { return this.tab.url || ""; }
  run(cmd, ...args) { const [func, world] = PAGE_COMMANDS[cmd]; return inPage(this.tab.id, func, checkedArgs(cmd, args), world); }
  async navigate(url) { await chrome.tabs.update(this.tab.id, { url: storeUrl(url) }); this.tab = await waitForLoad(this.tab.id); }
  async refresh() { try { this.tab = await chrome.tabs.get(this.tab.id); } catch (_) { /* closed */ } }   // where the tab is now (a page may have moved on)
  async signIn(site, robot = false) {
    this.keep = true;
    await chrome.tabs.update(this.tab.id, { active: true });
    return new Error(robot ? `Answer ${site}'s robot check in the tab that just opened, then import again.`
      : `Sign in to ${site} in the tab that just opened, then import again.`);
  }
  async close() { if (!this.keep) await closeTab(this.tab.id); }
}

// Where hidden frames live: Firefox's background page, or Chrome's offscreen document.
const frameHost = typeof document !== "undefined" ? {
  async open(name, url) {
    const f = document.createElement("iframe");
    // Sandboxed, so a store's page can't navigate the page that holds it (the store keeps its own origin and scripts).
    f.setAttribute("sandbox", "allow-scripts allow-same-origin allow-forms");
    Object.assign(f, { name, id: name, src: url, width: 1280, height: 900 });
    document.body.append(f);
  },
  async close(name) { document.getElementById(name)?.remove(); },
  async done() {},
} : {
  async open(name, url) {
    try {
      await chrome.offscreen.createDocument({ url: "offscreen.html", reasons: ["DOM_SCRAPING"],
        justification: "Reads your Amazon, Target, Costco and Carta pages, with your sign-in, without opening a tab." });
    } catch (e) {
      if (!/single offscreen|already/i.test(String(e && e.message))) throw new HiddenUnavailable(`offscreen: ${e.message}`);
    }
    for (let i = 0; ; i++) {
      let res;
      try { res = await chrome.runtime.sendMessage({ to: "offscreen", cmd: "open", name, url }); } catch (e) {
        if (i >= 10) throw new HiddenUnavailable(`offscreen: ${e.message}`);
        await sleep(100);
        continue;
      }
      if (res && res.ok === false) throw new HiddenUnavailable("offscreen: it refused the address");
      return res;
    }
  },
  async close(name) { await chrome.runtime.sendMessage({ to: "offscreen", cmd: "close", name }).catch(() => {}); },
  async done() { await chrome.offscreen.closeDocument().catch(() => {}); },
};

const hiddenPages = new Map();   // frame name -> HiddenPage
chrome.runtime.onConnect.addListener((port) => {
  const page = hiddenPages.get(port.name);
  if (!page || (port.sender && port.sender.id !== chrome.runtime.id)) { port.disconnect(); return; }
  // Only the hidden frame itself, not a frame a store page makes inside it with the same name (frame.js refuses
  // those too): the frame keeps its id as it loads one page after another.
  const frameId = port.sender && port.sender.frameId;
  if (page.frameId === undefined) page.frameId = frameId;
  if (frameId !== page.frameId) { port.disconnect(); return; }
  page.attach(port);
});

// A store page in a hidden frame. frame.js in it answers over a port, a new one each time the frame loads a page.
class HiddenPage {
  static async open(url) {
    url = storeUrl(url);
    const page = new HiddenPage(`runway-hidden-${crypto.randomUUID()}`);
    hiddenPages.set(page.name, page);
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
      if (m.error) p.reject(new Error(m.error)); else p.resolve(m.result);
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
    args = checkedArgs(cmd, args);
    for (let attempt = 0; ; attempt++) {
      if (!this.port) await this.loaded().catch(() => { throw new HiddenUnavailable("the hidden page stopped answering"); });
      const id = ++this.next;
      try {
        return await withTimeout(new Promise((resolve, reject) => {
          this.pending.set(id, { resolve, reject });
          this.port.postMessage({ id, cmd, args });
        }), PAGE_CALL_MS, "the hidden page stopped answering", HiddenUnavailable);
      } catch (e) {
        this.pending.delete(id);
        if (!e.moved || attempt >= 2) throw e;   // the page went elsewhere (a redirect): ask the new one
      }
    }
  }
  async navigate(url) {
    url = storeUrl(url);
    const ready = this.loaded();
    await this.run("go", url);
    await ready.catch(() => { throw new HiddenUnavailable(`${url} didn't load in a hidden frame`); });
  }
  async refresh() {}   // the frame reports its own address as it loads pages
  async signIn() { return new HiddenUnavailable("looked signed out"); }   // perhaps only in a frame: a tab will tell
  async close() {
    hiddenPages.delete(this.name);
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
  let complete = false;   // read back as far as Runway asked (not stopped by MAX_PAGES)
  try {
    if (/\/ap\/signin|\/ax\/claim/.test(page.url)) throw await page.signIn("Amazon");
    let html = (await page.run("html")).html;
    const orders = new Set();
    let seen = {};
    for (let n = 1; n <= MAX_PAGES; n++) {
      progress(`Reading Amazon payments, page ${n}…`);
      const r = await runway("/api/ext/amazon/transactions", { html, seen });
      r.orders.forEach((o) => orders.add(o));
      seen = r.seen || {};
      if (!r.next_form) { complete = true; break; }
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
    // A first read of each order doesn't count towards giving up on it (final: false); the second, below, does.
    await inParallel([...orders], AMAZON_PARALLEL, async (order) => {
      try {
        const res = await page.run("fetch", AMAZON_ORDER(order));
        const r = res.ok ? await runway("/api/ext/amazon/order", { order_number: order, html: res.text, final: false }) : { read: false };
        if (!r.read) unread.push(order);
      } catch (e) {
        if (stopsImport(e)) throw e;
        unread.push(order);
      }
      progress(`Reading Amazon orders, ${++done} of ${orders.size}…`);
      await sleep(PAUSE_MS);
    });
    let i = 0;
    for (const order of unread) {   // some pages only fill in once their scripts run: load each for real and read what shows
      progress(`Loading Amazon order ${++i} of ${unread.length}…`);
      try {
        await page.navigate(AMAZON_ORDER(order));
        await sleep(1500);
        const shown = await page.run("html");
        await runway("/api/ext/amazon/order", { order_number: order, html: shown.html, final: true });
      } catch (e) {   // one order that won't load doesn't stop the rest; it counts as a try, so it isn't asked for forever
        if (stopsImport(e)) throw e;
        console.warn(`Runway: Amazon order ${order}: ${e.message}`);
        await runway("/api/ext/amazon/order", { order_number: order, html: "", final: true }).catch(() => {});
      }
      await sleep(PAUSE_MS);
    }
  } catch (e) {
    if (e.code === "signin" || e.code === "robot") throw await page.signIn("Amazon", e.code === "robot");
    throw e;
  } finally {
    await page.close();
  }
  progress("Matching Amazon orders to your transactions…");
  return runway("/api/ext/finish", { retailer: "amazon", complete });
}

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

// ------------------------------------------------------------------------------------------------ Costco
//
// costco.com's own account page reads your receipts (warehouse, gas station, car wash) from a GraphQL service, signing
// each request with the sign-in it keeps in localStorage. The extension opens that page, then asks the service for your
// receipts a stretch of dates at a time (Runway says which service, which query and which headers), from inside the
// page, so the requests are the site's own kind. The sign-in never leaves the page: only the service's replies go to
// Runway, which reads them, and every receipt comes with its items, so there's no page per order to read.

const COSTCO_READY_MS = 12000;   // how long the account page gets to set its sign-in up (twice: once more after following its link)
const COSTCO_MAX_WINDOWS = 60;   // stretches of dates asked for (90 days each: fifteen years)
const costcoSignedOut = (url) => /\/(LogonForm|LogoffView)|signin\.costco\.com/i.test(url || "");

// Costco's form of a date for its receipts service: 9/01/2025 (month without a leading zero).
const costcoDate = (d) => `${d.getMonth() + 1}/${String(d.getDate()).padStart(2, "0")}/${d.getFullYear()}`;

// [from, to] stretches of at most `days` days covering `since` (YYYY-MM-DD) to today, newest first.
function costcoWindows(since, days, today = new Date()) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(since || "");
  const first = m ? new Date(+m[1], +m[2] - 1, +m[3]) : new Date(today.getFullYear(), today.getMonth(), today.getDate() - 180);
  const out = [];
  let end = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  while (end >= first && out.length < COSTCO_MAX_WINDOWS) {
    const from = new Date(end.getFullYear(), end.getMonth(), end.getDate() - (days - 1));
    const start = from < first ? first : from;
    out.push([start, end]);
    end = new Date(start.getFullYear(), start.getMonth(), start.getDate() - 1);
  }
  return out;
}

// Waits for the account page to set up its sign-in, and for it to be a good one: the token lasts fifteen minutes, and
// the page swaps in a new one as it loads (so one left over from your last visit is waited out, and if the page
// doesn't renew it, loaded again once). If the page doesn't set anything up by itself, follows its Orders & Purchases
// link. Signed out ends the import; a hidden frame that never gets a sign-in falls back to a tab.
async function costcoReady(page, g) {
  let followed = false, reloaded = false;
  for (let waited = 0; waited < COSTCO_READY_MS * 2; waited += 1000) {
    await sleep(1000);
    let state;
    try {
      state = await page.run("costcoState", g.storage_headers);
    } catch (e) {
      if (stopsImport(e)) throw e;
      await page.refresh();   // a tab that moved to Costco's sign-in page can't be read: that's signed out
      if (costcoSignedOut(page.url)) throw await page.signIn("Costco");
      continue;
    }
    if (state.ready) return;
    if (state.signedOut || costcoSignedOut(state.url)) throw await page.signIn("Costco");
    if (waited >= COSTCO_READY_MS) {
      if (state.stale && !reloaded) {   // a new load makes the page renew its sign-in
        reloaded = true;
        await page.navigate(g.page);
      } else if (!state.stale && !followed && state.link) {   // the account app's own Orders & Purchases page
        followed = true;
        await page.run("go", state.link);
      }
    }
  }
  if (page.hidden) throw new HiddenUnavailable("no sign-in for Costco's order service in the hidden page");
  throw new Error("Couldn't find how costco.com signs its order requests. Sign in to costco.com in this browser and open " +
    "Account → Orders & Purchases once, then import again. If that doesn't help, Runway may need an update for Costco's site.");
}

// One reply from Costco's order service as JSON. Signed out (401/403 twice over, in case the page was still
// refreshing its sign-in) ends the import; so does anything but an answer, which is kept for the next import.
async function costcoQuery(page, g, body) {
  for (let attempt = 0; ; attempt++) {
    const res = await page.run("costcoFetch", g.url, { method: "POST", headers: g.headers, body: JSON.stringify(body) }, g.storage_headers);
    if (res.signedOut || res.status === 401 || res.status === 403) {
      if (!res.signedOut && attempt < 2) { await sleep(2000); await costcoReady(page, g); continue; }   // perhaps the token ran out: wait for a new one
      throw Object.assign(new Error("signin"), { signin: true });
    }
    if (!res.ok) {
      throw Object.assign(new Error(`Costco stopped answering (${res.status ? `it answered ${res.status}` : res.error || "no reply"}). ` +
        "Runway kept what it read and will read the rest next time."), { limited: true });
    }
    try { return JSON.parse(res.text); } catch (_) {
      throw Object.assign(new Error("Costco's order service didn't answer with data. Runway kept what it read."), { limited: true });
    }
  }
}

async function importCostco(progress, Page) {
  const start = await runway("/api/ext/start", { retailer: "costco" });
  const g = start.graphql;
  if (!g) throw new Error("This Runway doesn't know how to read Costco yet. Update Runway, then import again.");
  progress("Opening your Costco account…");
  const page = await Page.open(g.page);
  let complete = true;   // every stretch of dates Runway asked for was read
  try {
    if (costcoSignedOut(page.url)) throw await page.signIn("Costco");
    await costcoReady(page, g);
    const windows = costcoWindows(start.since, g.max_days);
    try {
      let n = 0;
      for (const [from, to] of windows) {
        progress(`Reading Costco receipts, ${costcoDate(from)} to ${costcoDate(to)} (${++n} of ${windows.length})…`);
        const data = await costcoQuery(page, g, {
          query: g.query, variables: { ...g.variables, startDate: costcoDate(from), endDate: costcoDate(to) },
        });
        await runway("/api/ext/costco/history", { data, start: costcoDate(from), end: costcoDate(to) });
        if (n < windows.length) await sleep(1500 + Math.random() * 1000);
      }
    } catch (e) {
      if (e.signin) throw await page.signIn("Costco");
      if (e.limited) {   // keep what was read (matched now), without moving the last import's date on
        progress("Matching Costco receipts to your transactions…");
        await runway("/api/ext/finish", { retailer: "costco", complete: false }).catch(() => {});
      }
      throw e;
    }
  } catch (e) {
    if (e.code === "signin") throw await page.signIn("Costco");
    throw e;
  } finally {
    await page.close();
  }
  progress("Matching Costco receipts to your transactions…");
  return runway("/api/ext/finish", { retailer: "costco", complete });
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
    try { storeUrl(url); } catch (_) { return; }   // only Carta's own addresses
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
    let live = true;   // progress messages still arriving after the run ends are dropped
    const keepAlive = setInterval(() => chrome.runtime.getPlatformInfo(() => {}), 20000);   // a long import outlives the idle timer
    const { results = {} } = await store.get("results");
    try {
      const joined = (r) => !!results[r]?.ok;
      // "Import all": Amazon and Target, and Costco once it has worked; the daily import adds Carta too.
      const { cartaEvery } = await settings();
      // A little under the whole period, so the alarm's timing can't push a weekly read to the eighth day.
      const cartaDue = () => !results.carta?.at || Date.now() - Date.parse(results.carta.at) >= (cartaEvery * 24 - 2) * 3600e3;
      const everyday = which === "daily" ? [...EVERYDAY, ...JOINS_ONCE_WORKED.filter((r) => joined(r) && (r !== "carta" || cartaDue()))]
        : [...EVERYDAY, ...(joined("costco") ? ["costco"] : [])];
      for (const retailer of which === "all" || which === "daily" ? everyday : [which]) {
        const progress = (message) => { if (live) setStatus({ running: true, retailer, message }); };
        try {
          const r = await importStore(retailer, { amazon: importAmazon, target: importTarget, costco: importCostco, carta: importCarta }[retailer], progress);
          results[retailer] = { ok: true, at: new Date().toISOString(), message: summary(r), data: r };
        } catch (e) {
          results[retailer] = { ok: false, at: new Date().toISOString(), message: e.message || String(e) };
        }
        await store.set({ results });
      }
    } finally {
      live = false;
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

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (sender.id !== chrome.runtime.id) return;   // only the extension's own pages (popup, options)
  if (msg && msg.type === "refresh") refreshUnmatched().catch(() => {}).finally(() => reply({ ok: true }));
  if (msg && msg.type === "import" && (msg.retailer === "all" || RETAILERS[msg.retailer])) {
    run(msg.retailer);
    reply({ started: true });
  }
  if (msg && msg.type === "settings-changed") scheduleAuto().then(() => reply({ ok: true }));
  return true;
});

// Once a day, if you asked for it (Options): the same import, out of sight. An alarm already set is kept, so
// restarting Chrome doesn't start an extra import (Chrome keeps alarms across restarts).
async function scheduleAuto() {
  const { auto } = await settings();
  if (!auto) return chrome.alarms.clear("daily");
  if (!(await chrome.alarms.get("daily"))) chrome.alarms.create("daily", { delayInMinutes: 5, periodInMinutes: 24 * 60 });
}
chrome.alarms.onAlarm.addListener((a) => { if (a.name === "daily") run("daily"); });
chrome.runtime.onInstalled.addListener((d) => {
  scheduleAuto();
  store.remove("hiddenOff");   // a new version: try hidden frames again for every store
  if (d.reason === "install") chrome.runtime.openOptionsPage();
});
chrome.runtime.onStartup.addListener(scheduleAuto);
setStatus({ running: false, retailer: null, message: "" });
