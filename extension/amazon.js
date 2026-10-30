// Amazon: your payments pages, then each order's page.
/* global runway, MAX_PAGES, sleep, PAUSE_MS, inParallel, stopsImport -- from the other files here (see background.js) */
/* exported importAmazon */

const AMAZON = "https://www.amazon.com";
const AMAZON_TRANSACTIONS = `${AMAZON}/cpe/yourpayments/transactions`;
const AMAZON_ORDER = (n) => `${AMAZON}/gp/your-account/order-details?orderID=${encodeURIComponent(n)}`;
// Where Amazon's newer Transactions page asks for its further pages (as data rather than a page of HTML)
const AMAZON_TRANSACTION_DATA = `${AMAZON}/payments-portal/data/iris/live/v1/data/manage/get-transactions`;

// How to ask Amazon for the page after this one: what Runway's `next_form` says. The older page posts its form back
// to itself; the newer one asks its data address for the transactions after `exclusiveStartKey`, as its own page does.
function amazonNextPage(next) {
  if (next.exclusiveStartKey) {
    return [AMAZON_TRANSACTION_DATA, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json", "x-amzn-upx-token": String(next.token || "") },
      body: JSON.stringify({ type: "GetTransactions", locale: next.locale, exclusiveStartKey: next.exclusiveStartKey, widgetName: next.widgetName }),
    }];
  }
  return [AMAZON_TRANSACTIONS, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams(next).toString(),
  }];
}



const AMAZON_PARALLEL = 4;   // order pages read at once: quicker, and still a light load on Amazon



// ------------------------------------------------------------------------------------------------ Amazon

async function importAmazon(progress, Page) {
  await runway("/api/ext/start", { retailer: "amazon" });
  progress("Opening your Amazon payments…");
  const page = await Page.open(AMAZON_TRANSACTIONS);
  let complete = false;   // read back as far as Runway asked (not stopped by MAX_PAGES)
  try {
    if (/\/ap\/signin|\/ax\/claim/.test(page.url)) throw await page.signIn("Amazon");
    let sent = { html: (await page.run("html")).html };
    const orders = new Set();
    let seen = {};
    for (let n = 1; n <= MAX_PAGES; n++) {
      progress(`Reading Amazon payments, page ${n}…`);
      const r = await runway("/api/ext/amazon/transactions", { ...sent, seen });
      r.orders.forEach((o) => orders.add(o));
      seen = r.seen || {};
      if (r.repeated) break;   // Amazon gave a page it had already given: not all read (complete stays false)
      if (!r.next_form) { complete = true; break; }
      const res = await page.run("fetch", ...amazonNextPage(r.next_form));
      if (!res.ok) throw new Error(`Amazon's payments page answered ${res.status}.`);
      if (r.next_form.exclusiveStartKey) {
        let data;
        try { data = JSON.parse(res.text); } catch (e) { throw new Error("Amazon's payments page didn't answer with its transactions.", { cause: e }); }
        sent = { data, page: r.next_form };
      } else {
        sent = { html: res.text };
      }
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
