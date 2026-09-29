// Chrome's hidden page for the extension: it holds the store frames the extension reads in, so no tab or window
// opens. (Firefox's extension background is a page already, and holds them itself.)
chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (!msg || msg.to !== "offscreen") return;
  if (msg.cmd === "open") {
    const f = document.createElement("iframe");
    Object.assign(f, { name: msg.name, id: msg.name, src: msg.url, width: 1280, height: 900 });
    document.body.append(f);
  }
  if (msg.cmd === "close") document.getElementById(msg.name)?.remove();
  reply({ ok: true });
});
