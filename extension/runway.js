// The Runway side: the address and key from Options, the status the popup shows, and the calls to Runway.
/* exported setStatus, runway */

const RUNWAY_CALL_MS = 120000;  // and Runway

const store = chrome.storage.local;



// How often the daily import reads Carta, in days (Options): 1 = every daily import, 7 = weekly, 30 = monthly. Carta signs you
// out often, so reading it less often means fewer times you need to sign in again.
const cartaDays = (v) => ([1, 7, 30].includes(Number(v)) ? Number(v) : 1);
async function settings() {
  const s = await store.get(["runwayUrl", "token", "auto", "cartaEvery"]);
  return { runwayUrl: (s.runwayUrl || "").replace(/\/+$/, ""), token: s.token || "", auto: !!s.auto, cartaEvery: cartaDays(s.cartaEvery) };
}



// One write at a time, in order, so a late progress message can't land after "done" and leave the popup "running".
let statusChain = Promise.resolve();
function setStatus(patch) {
  statusChain = statusChain.then(async () => {
    const { status = {} } = await store.get("status");
    await store.set({ status: { ...status, ...patch, at: new Date().toISOString() } });
  }).catch(() => {});
  return statusChain;
}



async function runway(path, body) {
  const { runwayUrl, token } = await settings();
  if (!runwayUrl || !token) throw new Error("Set your Runway address and key in this extension's options first.");
  let res;
  try {
    res = await fetch(runwayUrl + path, {
      method: "POST",
      headers: { "Authorization": `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
      signal: AbortSignal.timeout(RUNWAY_CALL_MS),
      redirect: "error",   // the key and the body only ever go to the address that was saved, never wherever it points
    });
  } catch (e) {
    throw new Error(`Couldn't reach Runway at ${runwayUrl} (${e.message}).`, { cause: e });
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(data.error || `Runway answered ${res.status}.`), { code: data.code });
  return data;
}
