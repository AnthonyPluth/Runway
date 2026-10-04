// Chrome's hidden page for the extension: it holds the store frames the extension reads in, so no tab or window
// opens. (Firefox's extension background is a page already, and holds them itself.)
// The same addresses as storeUrl in stores.js (which can't be imported here): https on a store's own host.
const STORE_HOSTS = ["amazon.com", "target.com", "carta.com", "costco.com"];
function storeFrame(url) {
  try {
    const u = new URL(url);
    return u.protocol === "https:" && !u.username && !u.password && STORE_HOSTS.some((h) => u.hostname === h || u.hostname.endsWith("." + h));
  } catch (_) { return false; }
}

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (sender.id !== chrome.runtime.id || !msg || msg.to !== "offscreen") return;
  if (msg.cmd === "open") {
    if (!storeFrame(msg.url)) { reply({ ok: false }); return; }
    const f = document.createElement("iframe");
    // Sandboxed, so a store's page can't navigate this one (the store keeps its own origin and scripts).
    f.setAttribute("sandbox", "allow-scripts allow-same-origin allow-forms");
    Object.assign(f, { name: msg.name, id: msg.name, src: msg.url, width: 1280, height: 900 });
    document.body.append(f);
  }
  if (msg.cmd === "close") document.getElementById(msg.name)?.remove();
  reply({ ok: true });
});
