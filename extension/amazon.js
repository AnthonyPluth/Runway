// Amazon: your payments pages, then each order's page.
/* global runway, MAX_PAGES, sleep, PAUSE_MS, inParallel, stopsImport -- from the other files here (see background.js) */
/* exported importAmazon */

const AMAZON = "https://www.amazon.com";
const AMAZON_TRANSACTIONS = `${AMAZON}/cpe/yourpayments/transactions`;
const AMAZON_ORDER = (n) => `${AMAZON}/gp/your-account/order-details?orderID=${encodeURIComponent(n)}`;



const AMAZON_PARALLEL = 4;   // order pages read at once: quicker, and still a light load on Amazon



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
