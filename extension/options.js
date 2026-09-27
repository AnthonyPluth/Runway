const $ = (s) => document.querySelector(s);

async function load() {
  const s = await chrome.storage.local.get(["runwayUrl", "token", "auto"]);
  $("#url").value = s.runwayUrl || "";
  $("#token").value = s.token || "";
  $("#auto").checked = !!s.auto;
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
  const origin = url.origin;
  const token = $("#token").value.trim();
  if (!token) return show("Paste the key from Runway's Settings → Connections.", false);
  // Permission to talk to your Runway (and nowhere else besides the two stores).
  const granted = await chrome.permissions.request({ origins: [`${origin}/*`] }).catch(() => false);
  if (!granted) return show("The extension needs permission to reach your Runway to send it your orders.", false);
  await chrome.storage.local.set({ runwayUrl: origin + url.pathname.replace(/\/+$/, ""), token, auto: $("#auto").checked });
  await chrome.runtime.sendMessage({ type: "settings-changed" });
  try {
    const res = await fetch(`${origin}${url.pathname.replace(/\/+$/, "")}/api/ext/ping`, {
      method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: "{}",
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) return show(data.error || `Runway answered ${res.status}.`, false);
    show("Saved. Runway answered, so you're ready to import from the toolbar button.", true);
  } catch (e) {
    show(`Saved, but Runway didn't answer at ${origin} (${e.message}).`, false);
  }
});
load();
