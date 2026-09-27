const $ = (s) => document.querySelector(s);
const ago = (iso) => {
  const s = (Date.now() - new Date(iso)) / 1000;
  return s < 90 ? "just now" : s < 5400 ? `${Math.round(s / 60)} min ago` : s < 129600 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} days ago`;
};

async function render() {
  const { runwayUrl, token, status = {}, results = {} } = await chrome.storage.local.get(["runwayUrl", "token", "status", "results"]);
  $("#setup").hidden = !!(runwayUrl && token);
  $("#ready").hidden = !(runwayUrl && token);
  for (const el of document.querySelectorAll("[data-store]")) {
    const r = results[el.dataset.store];
    const line = el.querySelector(".result");
    line.className = `muted result ${r ? (r.ok ? "ok" : "bad") : ""}`;
    line.textContent = r ? `${r.message} (${ago(r.at)})` : "Not imported yet";
  }
  $("#progress").hidden = !status.running;
  $("#progress").textContent = status.message || "Working…";
  document.querySelectorAll("[data-import]").forEach((b) => { b.disabled = !!status.running; });
}

document.querySelectorAll("[data-import]").forEach((b) => b.addEventListener("click", () => {
  chrome.runtime.sendMessage({ type: "import", retailer: b.dataset.import });
}));
$("#open-options").addEventListener("click", () => chrome.runtime.openOptionsPage());
$("#options-link").addEventListener("click", (e) => { e.preventDefault(); chrome.runtime.openOptionsPage(); });
chrome.storage.onChanged.addListener(render);
render();
