// Runway orders: reads your Amazon, Target and Costco order history with the sign-in already in this browser and sends it to
// your Runway, which does all the reading of those pages. Nothing goes anywhere but the Runway address you set.
//
// Each store is read in a hidden page of its own site (see frames.js), so nothing opens while it works.

// The other files here are classic scripts that share this one's global scope: Chrome's worker loads them with
// importScripts, Firefox loads them from the manifest's background "scripts" (keep the two lists the same, in order).
if (typeof importScripts === "function") {
  importScripts("page.js", "util.js", "runway.js", "stores.js", "frames.js", "amazon.js", "target.js", "costco.js", "carta.js");
}
/* global store, settings, setStatus, importStore, importAmazon, importTarget, importCostco, importCarta, runway -- from the other files here */

const RETAILERS = { amazon: "Amazon", target: "Target", costco: "Costco", carta: "Carta" };
const EVERYDAY = ["amazon", "target"];   // "Import all"; Costco and Carta have their own buttons (and join once they've worked)
const JOINS_ONCE_WORKED = ["costco", "carta"];   // in the daily import; Costco in "Import all" too (Carta stays out of it)

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
