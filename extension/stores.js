// The store addresses the extension may read or send a page to. Nothing else is ever opened or fetched.
/* exported STORE_HOSTS, STORE_MATCHES, checkedArgs */

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
