const $ = (s) => document.querySelector(s);

async function load() {
  const s = await chrome.storage.local.get(["runwayUrl", "token", "auto", "cartaEvery"]);
  $("#url").value = s.runwayUrl || "";
  $("#token").value = s.token || "";
  $("#auto").checked = !!s.auto;
  $("#carta").value = ["1", "7", "30"].includes(String(s.cartaEvery)) ? String(s.cartaEvery) : "1";
}

// This computer, a private network address (10.x, 172.16-31.x, 192.168.x, 100.64-127.x for Tailscale and the
// like, IPv6 local ones), or a local-only name.
function privateHost(host) {
  const h = host.replace(/^\[|\]$/g, "").toLowerCase();
  if (h === "localhost" || /\.(local|lan|home\.arpa|internal|localhost)$/.test(h) || !h.includes(".") && !h.includes(":")) return true;
  const ip = h.split(".").map(Number);
  if (ip.length === 4 && ip.every((n) => Number.isInteger(n) && n >= 0 && n <= 255)) {
    const [a, b] = ip;
    return a === 127 || a === 10 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168) || (a === 100 && b >= 64 && b <= 127);
  }
  return h === "::1" || (h.includes(":") && /^(f[cd]|fe80:)/.test(h));
}

function show(msg, ok) {
  $("#result").textContent = msg;
  $("#result").className = ok ? "ok" : "bad";
}

$("#save").addEventListener("click", async () => {
  let url;
  try {
    url = new URL($("#url").value.trim());
    if (!/^https?:$/.test(url.protocol)) throw new Error();
  } catch (_) { return show("Enter Runway's address, like https://runway.example.com", false); }
  // The key goes with every request, so plain http only on this computer or your own network.
  if (url.protocol === "http:" && !privateHost(url.hostname)) {
    return show("Use https for a Runway on the internet (plain http would send your key unencrypted).", false);
  }
  const origin = url.origin;
  const token = $("#token").value.trim();
  if (!token) return show("Paste the key from Runway's Settings → Connections.", false);
  // Permission to talk to your Runway (and nowhere else besides the two stores).
  const granted = await chrome.permissions.request({ origins: [`${origin}/*`] }).catch(() => false);
  if (!granted) return show("The extension needs permission to reach your Runway to send it your orders.", false);
  const { runwayUrl: oldUrl } = await chrome.storage.local.get("runwayUrl");
  await chrome.storage.local.set({ runwayUrl: origin + url.pathname.replace(/\/+$/, ""), token, auto: $("#auto").checked, cartaEvery: Number($("#carta").value) });
  // Moved to another address: the old one no longer needs the extension's access (best effort; a store's stays).
  try {
    const oldOrigin = oldUrl && new URL(oldUrl).origin;
    if (oldOrigin && oldOrigin !== origin) await chrome.permissions.remove({ origins: [`${oldOrigin}/*`] });
  } catch (_) { /* not granted, or one the extension always has */ }
  await chrome.runtime.sendMessage({ type: "settings-changed" });
  try {
    const res = await fetch(`${origin}${url.pathname.replace(/\/+$/, "")}/api/ext/ping`, {
      method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: "{}",
      redirect: "error",   // the key goes only to the address entered, never wherever it points
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) return show(data.error || `Runway answered ${res.status}.`, false);
    show("Saved. Runway answered, so you're ready to import from the toolbar button.", true);
  } catch (e) {
    show(`Saved, but Runway didn't answer at ${origin} (${e.message}).`, false);
  }
});
load();
