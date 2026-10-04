// Carta: the data addresses its pages load your holdings from.
/* global runway, storeUrl, sleep -- from the other files here (see background.js) */
/* exported importCarta */

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
