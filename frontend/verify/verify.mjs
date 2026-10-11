// Drives the running app with Playwright and collects proof: a screenshot of each page (full page, and the top of it) at phone, tablet and desktop
// widths, plus console errors and failed requests. Started by `python run.py verify` (runway/verify.py), which owns the
// demo database and the server; run alone it needs a server that already has the demo data.
//
//   node verify/verify.mjs --url http://127.0.0.1:8765 --out ../artifacts/verify [page…]
//
// Exits 1 on a console error, an uncaught page error, a 5xx response or a failed scripted step.
import { chromium } from "@playwright/test";
import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export const VIEWPORTS = { phone: { width: 390, height: 844 }, tablet: { width: 768, height: 1024 }, desktop: { width: 1280, height: 800 } };
export const PAGES = ["overview", "transactions", "budget", "recurring", "networth", "reports", "churning", "setup"];

/** The browser to launch: the Chromium preinstalled under PLAYWRIGHT_BROWSERS_PATH (or /opt/pw-browsers) when there is
 *  one, whatever its revision, otherwise undefined (Playwright's own download). */
export function findChromium(env = process.env, exists = existsSync, list = readdirSync) {
  const root = env.PLAYWRIGHT_BROWSERS_PATH || "/opt/pw-browsers";
  if (!exists(root)) return undefined;
  for (const dir of list(root).filter((d) => d.startsWith("chromium-")).sort().reverse()) {
    for (const sub of ["chrome-linux64/chrome", "chrome-linux/chrome"]) {
      const p = join(root, dir, sub);
      if (exists(p)) return p;
    }
  }
  return undefined;
}

/** The two files one screenshot makes: the full page, and `-top`, just the viewport (the top of the page), which is the one
 *  that fits in a pull request (`make pr-screenshots`). */
export const screenshotFiles = (name, viewport) => ({ full: `${name}-${viewport}.png`, top: `${name}-${viewport}-top.png` });

/** A flow is { name, page?, viewports?, signed_in?, steps: [ { goto | click | fill | press | wait_for | scroll_to | expect_text | screenshot | reload | authenticator } ] }:
 *  see frontend/verify/flows/README.md. Returns the problems with it, [] when it's well formed. */
const ACTIONS = { goto: "string", click: "string", fill: "object", press: "object", wait_for: "string", scroll_to: "string", expect_text: "object", screenshot: "string",
  reload: "boolean", authenticator: "string" };
// The virtual authenticator's states an `authenticator` step sets (a signed_in flow's): Face ID that works, or that fails.
const AUTHENTICATOR = ["verified", "unverified"];
export const unknownPages = (names) => names.filter((n) => !PAGES.includes(n));

export function flowProblems(flow) {
  const out = [];
  if (!flow || typeof flow.name !== "string" || !/^[\w-]+$/.test(flow.name)) out.push("needs a name of letters, digits, - or _");
  if (!Array.isArray(flow?.steps) || !flow.steps.length) out.push("needs a list of steps");
  for (const [i, s] of (flow?.steps ?? []).entries()) {
    const keys = Object.keys(s).filter((k) => k !== "timeout");
    if (keys.length !== 1 || !(keys[0] in ACTIONS)) out.push(`step ${i + 1} must have exactly one of ${Object.keys(ACTIONS).join(", ")}`);
    else if (typeof s[keys[0]] !== ACTIONS[keys[0]]) out.push(`step ${i + 1}: ${keys[0]} takes a ${ACTIONS[keys[0]]}`);
    else if (keys[0] === "authenticator" && !flow.signed_in) out.push(`step ${i + 1}: authenticator needs "signed_in": true`);
    else if (keys[0] === "authenticator" && !AUTHENTICATOR.includes(s.authenticator)) out.push(`step ${i + 1}: authenticator is ${AUTHENTICATOR.join(" or ")}`);
  }
  if (flow?.signed_in !== undefined && typeof flow.signed_in !== "boolean") out.push("signed_in is true or false");
  if (flow?.page !== undefined && !PAGES.includes(flow.page)) out.push(`unknown page ${flow.page}`);
  for (const v of flow?.viewports ?? []) if (!(v in VIEWPORTS)) out.push(`unknown viewport ${v}`);
  return out;
}

async function runStep(page, step, shot) {
  const timeout = step.timeout ?? 10000;
  if ("goto" in step) {
    await page.goto(step.goto.startsWith("#") ? `${page.url().split("#")[0]}${step.goto}` : step.goto);
  } else if ("click" in step) {
    await page.locator(step.click).first().click({ timeout });
  } else if ("fill" in step) {
    await page.locator(step.fill.selector).first().fill(step.fill.text, { timeout });
  } else if ("press" in step) {
    await page.locator(step.press.selector).first().press(step.press.key, { timeout });
  } else if ("wait_for" in step) {
    await page.locator(step.wait_for).first().waitFor({ timeout });
  } else if ("scroll_to" in step) {   // so a screenshot of the viewport shows it
    // It then waits until the element has stopped moving (smooth scrolling and scroll snapping animate), so the screenshot after
    // it shows where it came to rest.
    await page.locator(step.scroll_to).first().evaluate((el) => new Promise((done) => {
      el.scrollIntoView({ block: "center", inline: "start" });
      let last = "", still = 0, frames = 0;
      const tick = () => {
        const r = el.getBoundingClientRect();
        const at = `${Math.round(r.left)},${Math.round(r.top)}`;
        still = at === last ? still + 1 : 0;
        last = at;
        if (still >= 8 || ++frames > 180) done(undefined); else setTimeout(tick, 16);
      };
      setTimeout(tick, 16);
    }), undefined, { timeout });
  } else if ("expect_text" in step) {
    const el = page.locator(step.expect_text.selector).first();
    await el.waitFor({ timeout });
    const got = await el.innerText();
    if (!got.includes(step.expect_text.text)) throw new Error(`expected "${step.expect_text.text}" in ${step.expect_text.selector}, found "${got.slice(0, 80)}"`);
  } else if ("screenshot" in step) {
    await shot(step.screenshot);
  } else if ("reload" in step) {   // opening the app again (a launch), not just another route
    await page.reload({ waitUntil: "networkidle", timeout });
  } else if ("authenticator" in step) {
    const { cdp, authenticatorId } = page.authenticator;
    await cdp.send("WebAuthn.setUserVerified", { authenticatorId, isUserVerified: step.authenticator === "verified" });
  }
}

/** A signed_in flow's browser: the signed-in demo server's session cookie, and a virtual platform authenticator (Chrome's
 *  DevTools WebAuthn domain) standing in for Face ID or Touch ID, which verifies the person until a step says otherwise.
 *  `prf`: the authenticator gives a PRF secret (as a passkey on a current iPhone, Android or Mac would), so the app keeps
 *  its encrypted cache; without it (the flows), it's a passkey without PRF, and the app keeps none. */
async function signIn(ctx, page, url, token, { prf = false } = {}) {
  await ctx.addCookies([{ name: "runway_session", value: token, url }]);
  const cdp = await ctx.newCDPSession(page);
  await cdp.send("WebAuthn.enable");
  const { authenticatorId } = await cdp.send("WebAuthn.addVirtualAuthenticator", { options: {
    protocol: "ctap2", transport: "internal", hasResidentKey: true, hasUserVerification: true, isUserVerified: true,
    automaticPresenceSimulation: true, ...(prf ? { hasPrf: true } : {}) } });
  page.authenticator = { cdp, authenticatorId };
}

/** What's in the device cache's IndexedDB (lib/idb.ts) in this page: each row, its bytes as latin1 text, so a test can
 *  look for plaintext in it; null when there's no such database. */
const cacheRows = (page) => page.evaluate(async () => {
  if (!(await globalThis.indexedDB.databases()).some((d) => d.name === "runway-cache")) return null;
  const db = await new Promise((ok, no) => { const r = globalThis.indexedDB.open("runway-cache"); r.onsuccess = () => ok(r.result); r.onerror = () => no(r.error); });
  try {
    if (!db.objectStoreNames.contains("rows")) return [];
    const rows = await new Promise((ok, no) => { const r = db.transaction("rows").objectStore("rows").getAll(); r.onsuccess = () => ok(r.result); r.onerror = () => no(r.error); });
    const text = (b) => { const u = b instanceof ArrayBuffer ? new Uint8Array(b) : b; let s = ""; for (const x of u) s += String.fromCharCode(x); return s; };
    return rows.map((r) => Object.fromEntries(Object.entries(r).map(([k, v]) => [k, v instanceof ArrayBuffer || ArrayBuffer.isView(v) ? text(v) : v])));
  } finally { db.close(); }
});

/** Polls `test` (async) until it's true, or fails with `what` after `timeout` ms. */
async function until(test, what, timeout = 10000) {
  const end = Date.now() + timeout;
  while (!(await test())) {
    if (Date.now() > end) throw new Error(`timed out: ${what}`);
    await new Promise((r) => setTimeout(r, 100));
  }
}

/** The encrypted cache on the device (#379), end to end, with a passkey that gives a PRF secret: after an unlock the
 *  Transactions list is kept sealed (none of its text readable in IndexedDB); a cold start (a reload, then the unlock)
 *  paints it from the device before the server's list arrives (held back here), then the server's replaces it; past the
 *  72-hour window it isn't painted (it's wiped at launch); signing out leaves nothing in IndexedDB. It signs the shared
 *  session out at the end, so it runs after every flow. */
async function cacheCheck(page, base) {
  const SLOW_MS = 6000;
  // The lock screen offers the unlock on its own; should the browser want a tap first, the button.
  const launch = async () => {
    await page.reload({ waitUntil: "load" });
    void page.locator("button:text-is('Unlock'):not([disabled])").click({ timeout: 8000 }).catch(() => { /* unlocked on its own */ });
  };
  await page.clock.install();   // (time runs as usual; jumped ahead below)
  await page.goto(`${base}/#setup/data`, { waitUntil: "networkidle" });
  await page.locator("button:text-is('Turn on app lock')").first().click();
  await page.locator("text=On for this device").first().waitFor();
  // A launch: the lock screen, and the unlock it offers on its own (the authenticator verifies).
  await page.goto(`${base}/#transactions`);
  await launch();
  await page.locator("[data-testid=tx-rows][aria-busy=false] [data-tx-list]").waitFor({ timeout: 20000 });
  await until(async () => (await cacheRows(page))?.some((r) => r.k.startsWith("r:transactions?")), "the Transactions list kept on the device");
  const rows = await cacheRows(page);
  if (!rows.some((r) => r.k === "share")) throw new Error("no sealed copy of the share on the device");
  const words = [...new Set((await page.locator("[data-tx-list]").innerText()).split(/\s+/).filter((w) => /^[A-Za-z]{5,}$/.test(w)))].slice(0, 20);
  const stored = JSON.stringify(rows);
  const readable = words.filter((w) => stored.includes(w));
  if (!words.length || readable.length) throw new Error(`the cache isn't sealed: ${readable.join(", ") || "no words to look for"}`);
  for (const r of rows) for (const f of Object.keys(r)) if (!["k", "v", "version", "at", "iv", "ct", "device", "fetchedAt", "expiresAt"].includes(f)) throw new Error(`unexpected field ${f} in a stored row`);

  // Cold start, the server slow to send the list: painted from the device (marked as not yet current), then replaced.
  let held = 0, released = 0;
  await page.route(/\/api\/transactions\?/, async (route) => {
    held++;
    await new Promise((r) => setTimeout(r, SLOW_MS));
    released++;
    await route.continue().catch(() => { /* the page moved on */ });
  });
  await launch();
  await page.locator("[data-testid=tx-rows][aria-busy=true] [data-tx-list]").waitFor({ timeout: 20000 });
  if (!held || released) throw new Error("the list wasn't painted from the device before the server's arrived");
  await page.locator("[data-testid=tx-rows][aria-busy=false] [data-tx-list]").waitFor({ timeout: SLOW_MS + 10000 });

  // Past the window: the copy of the share is out of date at launch, so the cache goes, and nothing is painted from it.
  await page.clock.setSystemTime(Date.now() + 73 * 3600 * 1000);
  await launch();
  // Either the page's loading placeholder (from the server: right) or rows painted from the device (wrong) comes first.
  await page.locator("[role=status][aria-busy=true], [data-testid=tx-rows][aria-busy=true]").first().waitFor({ timeout: 20000 });
  if (await page.locator("[data-testid=tx-rows][aria-busy=true]").count()) throw new Error("painted from a cache past its window");
  await page.locator("[data-testid=tx-rows][aria-busy=false] [data-tx-list]").waitFor({ timeout: SLOW_MS + 10000 });
  await page.unroute(/\/api\/transactions\?/);

  // Signing out deletes it.
  await page.locator("a[aria-label='Sign out']").click();
  await page.waitForURL((u) => !u.hash.includes("transactions"), { timeout: 15000 }).catch(() => {});
  await page.goto(`${base}/auth/signed-out`).catch(() => {});
  const left = await cacheRows(page);
  if (left && left.length) throw new Error(`signing out left ${left.length} row(s) in IndexedDB`);
}

async function main() {
  const args = process.argv.slice(2);
  const opt = (name, fallback) => { const i = args.indexOf(name); return i < 0 ? fallback : args.splice(i, 2)[1]; };
  const base = opt("--url", process.env.RUNWAY_VERIFY_URL);
  const here = dirname(fileURLToPath(import.meta.url));
  const out = opt("--out", join(here, "../../artifacts/verify"));
  const flowsDir = opt("--flows", join(here, "flows"));
  // The signed-in demo server (runway/verify.py), for flows with "signed_in": true, and its session's token.
  const signedInUrl = opt("--signed-in-url", process.env.RUNWAY_VERIFY_SIGNED_IN_URL);
  const session = process.env.RUNWAY_VERIFY_SESSION;
  if (!base) { console.error("verify: no server address (--url or RUNWAY_VERIFY_URL)"); process.exit(2); }
  const unknown = unknownPages(args);
  if (unknown.length) { console.error(`verify: no such page: ${unknown.join(", ")} (pages: ${PAGES.join(", ")})`); process.exit(2); }
  const pages = args.length ? args : PAGES;

  const flows = existsSync(flowsDir) ? readdirSync(flowsDir).filter((f) => f.endsWith(".json")).sort().map((f) => {
    let flow;
    try { flow = JSON.parse(readFileSync(join(flowsDir, f), "utf8")); } catch (e) { console.error(`verify: flow ${f} isn't valid JSON (${e.message})`); process.exit(2); }
    const bad = flowProblems(flow);
    if (bad.length) { console.error(`verify: flow ${f}: ${bad.join("; ")}`); process.exit(2); }
    return flow;
  }) : [];

  rmSync(out, { recursive: true, force: true });
  mkdirSync(out, { recursive: true });
  const browser = await chromium.launch({ executablePath: findChromium() });
  const problems = [];   // what makes the run fail
  const notes = [];      // what's only reported
  const results = [];

  async function visit(label, viewport, work, signedIn = false, authenticator = {}) {
    const ctx = await browser.newContext({ viewport: VIEWPORTS[viewport] });
    const page = await ctx.newPage();
    if (signedIn) await signIn(ctx, page, signedInUrl, session, authenticator);
    const where = `${label} @ ${viewport}`;
    const consoleErrors = [];
    page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text()); });
    page.on("pageerror", (e) => consoleErrors.push(`uncaught: ${e.message}`));
    page.on("response", (r) => { if (r.status() >= 500) problems.push(`${where}: ${r.status()} from ${r.request().method()} ${new URL(r.url()).pathname}`); });
    page.on("requestfailed", (r) => { if (r.failure()?.errorText !== "net::ERR_ABORTED") notes.push(`${where}: request failed (${r.failure()?.errorText}) ${new URL(r.url()).pathname}`); });
    const files = [];
    const shot = async (name) => {
      const { full, top } = screenshotFiles(name, viewport);
      await page.screenshot({ path: join(out, full), fullPage: true });
      files.push(full);
      await page.screenshot({ path: join(out, top) });
      files.push(top);
    };
    try {
      await work(page, shot);
    } catch (e) {
      problems.push(`${where}: ${String(e.message).split("\n")[0]}`);
      // The failure screenshot is best effort: the step's own error is already recorded above.
      await shot(`${label.replace(/\W+/g, "-")}-FAILED`).catch(() => {});
    }
    for (const c of consoleErrors) problems.push(`${where}: console error: ${c.slice(0, 300)}`);
    results.push({ label, viewport, screenshots: files, consoleErrors });
    await ctx.close();
  }

  for (const name of pages) {
    for (const viewport of Object.keys(VIEWPORTS)) {
      await visit(name, viewport, async (page, shot) => {
        await page.goto(`${base}/#${name}`, { waitUntil: "networkidle" });
        await shot(name);
      });
    }
  }
  for (const flow of flows) {
    if (args.length && !args.includes(flow.page ?? "overview")) continue;
    if (flow.signed_in && !(signedInUrl && session)) { notes.push(`flow ${flow.name}: skipped (no signed-in server: run it with make verify)`); continue; }
    for (const viewport of flow.viewports ?? Object.keys(VIEWPORTS)) {
      await visit(`flow ${flow.name}`, viewport, async (page, shot) => {
        await page.goto(`${flow.signed_in ? signedInUrl : base}/#${flow.page ?? "overview"}`, { waitUntil: "networkidle" });
        for (const step of flow.steps) await runStep(page, step, (n) => shot(`flow-${flow.name}-${n}`));
        await shot(`flow-${flow.name}`);
      }, !!flow.signed_in);
    }
  }
  // Last: it signs the shared session out.
  if (!args.length || args.includes("transactions")) {
    if (signedInUrl && session) await visit("device cache", "desktop", (page) => cacheCheck(page, signedInUrl), true, { prf: true });
    else notes.push("device cache: skipped (no signed-in server: run it with make verify)");
  }
  await browser.close();

  writeFileSync(join(out, "report.json"), JSON.stringify({ base, results, problems, notes }, null, 2));
  const shots = results.reduce((n, r) => n + r.screenshots.length, 0);
  console.log(`verify: ${results.length} visits, ${shots} screenshots in ${out}`);
  for (const n of notes) console.log(`  note: ${n}`);
  for (const p of problems) console.error(`  FAIL: ${p}`);
  console.log(problems.length ? `verify: FAILED (${problems.length} problem${problems.length === 1 ? "" : "s"})` : "verify: OK, no console errors or 5xx responses");
  process.exit(problems.length ? 1 : 0);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) main().catch((e) => { console.error(e); process.exit(2); });
