// Runs only in the extension's own hidden frames (it names them "runway-hidden-…"), never in a store tab you have
// open: it answers the extension's requests (page.js) with this page's own sign-in, as a tab would.
(() => {
  // The extension's frame sits right in its hidden page; a frame the store's page makes inside it (even one named
  // the same) isn't it.
  if (window.top === window || window.parent !== window.top || !/^runway-hidden-/.test(window.name) || window.__runwayFrame) return;
  window.__runwayFrame = true;
  const port = chrome.runtime.connect({ name: window.name });
  port.onMessage.addListener(async (msg) => {
    let result, error;
    try {
      const [func] = PAGE_COMMANDS[msg.cmd];
      result = await func(...(msg.args || []));
    } catch (e) {
      error = String(e && e.message || e);
    }
    try { port.postMessage({ id: msg.id, result, error }); } catch (_) { /* the page moved on */ }
  });
})();
