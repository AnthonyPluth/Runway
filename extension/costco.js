// Costco: its account page's receipts service, asked a stretch of dates at a time.
/* global sleep, stopsImport, HiddenUnavailable, runway -- from the other files here (see background.js) */
/* exported importCostco */

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
