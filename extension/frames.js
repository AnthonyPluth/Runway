// Where a store is read: a hidden frame (Chrome's offscreen document, Firefox's background page) or, failing that, a
// background tab. HiddenPage and TabPage answer the same calls, so the importers don't care which they got.
/* global withTimeout, storeUrl, PAGE_COMMANDS, checkedArgs, sleep, STORE_HOSTS, STORE_MATCHES, store -- from the other files here (see background.js) */
/* exported stopsImport, importStore */

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

const PAGE_CALL_MS = 90000;     // longest a store page may take to answer the extension (its own fetches stop at 45s)



// Errors that end a store's import rather than skipping one order: signed out, a robot check, the store refusing
// more reads for now, or the hidden page giving out (the import starts again in a tab).
const stopsImport = (e) => e instanceof HiddenUnavailable || !!(e && (e.signin || e.limited || e.code === "signin" || e.code === "robot"));



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
