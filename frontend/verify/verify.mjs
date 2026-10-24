// Drives the running app with Playwright and collects proof: a full-page screenshot of each page at phone and desktop
// widths, plus console errors and failed requests. Started by `python run.py verify` (runway/verify.py), which makes the
// seeded demo data, retries what failed and decides what's kept.
//
//   node verify/verify.mjs --seed <seeded data folder> --work <folder> --python <python> --out ../artifacts/verify [--jobs 4]
//        [--time-scale 2] [--only page:budget,flow:x] [--reverse] [page or flow…]
//   node verify/verify.mjs --url http://127.0.0.1:8765 --out ../artifacts/verify [page or flow…]   (by hand, on a server already
//        running: one item at a time on it; the signed-in flows and the device cache check are skipped)
//
// Each page, flow and the device cache check is an item. With --seed every item gets a demo server and a copy of the
// seeded data of its own (runway/verify.py's `serve`), started for it and stopped after it, so what an item shows never
// depends on what another did; --jobs items run at once, each worker a browser of its own (jobs.mjs). With --url every
// item uses that one server, one at a time. report.json says, item by item, whether it passed and what it wrote
// (rewritten after each one, so a crash leaves the items it didn't reach marked as not run). An item fails on a console
// error, an uncaught page error, a 5xx response, a failed scripted step, a screenshot that never settled or an accessibility finding (a11y.mjs) that is not in a11y-baseline.txt.
// Exits 1 when one failed, 2 when the run can't start (a malformed flow, an unknown page).
import { chromium } from "@playwright/test";
import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { cpus, loadavg } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { auditVisit, inheritedWhere, inheritProblems, readBaseline, visitProblems } from "./a11y.mjs";
import { runPool, startServer } from "./jobs.mjs";

// The phone is an iPhone 17 Pro Max (440×956 CSS pixels). A page or flow is shot at phone and desktop only, which is what
// tools/fleet_checks.py reads to know the names of the images `make verify` writes.
export const VIEWPORTS = { phone: { width: 440, height: 956 }, desktop: { width: 1280, height: 800 } };
export const DEFAULT_VIEWPORTS = ["phone", "desktop"];
// Only a flow that lists it runs here (not every page): a phone turned sideways, which app.css still counts as a phone (short, touch-driven).
export const LANDSCAPE = { landscape: { width: 844, height: 390 } };
export const PAGES = ["overview", "transactions", "budget", "investments", "recurring", "networth", "reports", "churning", "setup"];

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

/** How a worker's browser is launched (control.mjs's session's too). No partial raster: with it, a tile that changed is
 *  rastered again only where it was invalidated and keeps the rest from an earlier frame, so an edge across that boundary
 *  (a card's rounded corner behind the emoji picker) came out a level or two apart depending on which frames were drawn:
 *  15 to 45% of the emoji picker's captures, against none of 152 without it (whatever the number of raster threads, which
 *  on its own changed nothing). One raster thread: a busy machine has no cores to spare anyway. */
export const launchOptions = () => ({ executablePath: findChromium(), args: ["--num-raster-threads=1", "--disable-partial-raster"] });

/** The moment the browser's clock starts at for a run frozen at `day` (YYYY-MM-DD): noon local time, as runway/verify.py's
 *  FROZEN_TIME is for the server (both read in the machine's time zone). The browser keeps it fixed for every visit but the device cache check, which installs a running clock from it so it can jump ahead. */
export const frozenTime = (day) => new Date(`${day}T12:00:00`);

/** Run in the page before a screenshot: waits for the fonts, and writes the demo servers' port (a free one each run) as a
 *  fixed number wherever the page shows it (the assistant's MCP address), so the image doesn't change with the port. */
export async function settle(keepScroll = false) {
  await globalThis.document.fonts.ready;
  // Wait until the page has stopped changing (a list that grows as it loads, a panel that opens over a few frames): its
  // height and text the same for 8 samples in a row, at most 4 seconds.
  const look = () => `${globalThis.document.documentElement.scrollHeight}|${globalThis.document.body.innerText.length}`;
  for (let i = 0, same = 0, last = look(); same < 8 && i < 80; i++) {
    await new Promise((r) => globalThis.requestAnimationFrame(() => setTimeout(r, 50)));
    const now = look();
    same = now === last ? same + 1 : 0;
    last = now;
  }
  // A last beat for what a frame-by-frame look can't see: focus moving into a popover, an icon settling.
  await new Promise((r) => setTimeout(r, 300));
  // Back to the top: a full-page capture draws sticky and fixed parts (the sidebar, a date header) where the page was scrolled
  // to, which depends on how far a click scrolled it. Not after a flow's own scroll_to, which put its target where it should
  // be (centred, a fixed place) and is what that flow shows.
  if (!keepScroll) globalThis.scrollTo({ top: 0, left: 0, behavior: "instant" });   // not the page's smooth scrolling, which would still be moving
  await new Promise((r) => globalThis.requestAnimationFrame(() => globalThis.requestAnimationFrame(r)));
  // A list that scrolls its active option into view (the category picker) is caught part way or at the end, run to run: start it at the top.
  for (const el of globalThis.document.querySelectorAll("[role=listbox]")) el.scrollTop = 0;
  await new Promise((r) => globalThis.requestAnimationFrame(() => globalThis.requestAnimationFrame(r)));
  const fixed = (t) => t.replace(/\b(localhost|127\.0\.0\.1):\d{2,5}/g, "$1:8080");
  const walker = globalThis.document.createTreeWalker(globalThis.document.body, 4);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) if (n.nodeValue && fixed(n.nodeValue) !== n.nodeValue) n.nodeValue = fixed(n.nodeValue);
  for (const el of globalThis.document.querySelectorAll("input, textarea")) if (fixed(el.value) !== el.value) el.value = fixed(el.value);
}

/** Toasts come and go on timers and stack with whatever else the run did, so what's on screen depends on timing: left out of the images. */
export const HIDE_TOASTS = "[data-sonner-toaster] { visibility: hidden !important; } .just-saved::after, [style*=saved-fade] { display: none !important; }";
/** Infinite animations (pulsing dots, skeletons, spinners) stopped for the capture. Not every animation (`animations: "disabled"`
 *  cancels them all, which closed a confirm dialog that was still waiting for its own): only these, which never end. */
export const STILL = ".animate-pulse, .animate-spin, .live-dot { animation: none !important; }";

/** In every page from the start: no smooth scrolling, no caret, and the transitions and the opening animations of
 *  dialogs, sheets and popovers (`[data-state=open]`) over at once, so a step after one sees where it ends and a capture
 *  never holds it part way. Not `animation: none`, which a dialog waiting for its animation's end would wait on forever:
 *  a zero length still ends, with its event. (A sheet that slid in kept the raster of its last animated frame, a
 *  fraction of a pixel off and different each run: budget-suggest's images differed run to run.) Closing animations
 *  stay: with them instant, the page behind a dialog closed and another opened came back scrolled elsewhere. */
export const PAGE_STYLE = "*, *::before, *::after { scroll-behavior: auto !important; caret-color: transparent !important; "
  + "transition-duration: 0s !important; transition-delay: 0s !important; } "
  + "[data-state=open], [data-state=open]::before, [data-state=open]::after { animation-duration: 0s !important; animation-delay: 0s !important; }";

/** The file one screenshot makes: the full page, so a change anywhere on it shows in the diff (`make screenshots`). */
export const screenshotFile = (name, viewport) => `${name}-${viewport}.png`;
/** Each page is also saved as its first screen, `<page>-top-<width>.png`: the window as it opens, before any scrolling, for the
 *  README and the docs site's hero images (a full page is too tall for those). tools/fleet_checks.py reads this name. */
export const TOP = "top";

/** page.screenshot's options for one capture: the full page, except the window as it shows when a modal is open or the
 *  shot is a page's first screen (`top`). */
export const captureOptions = ({ modal = false, top = false, timeout } = {}) =>
  ({ fullPage: !modal && !top, style: `${HIDE_TOASTS} ${STILL}`, timeout });

/** What on the page says it's still loading, [] when nothing does. Run in the page, so it stands alone. A screenshot is
 *  only taken when this is empty, and checked again after: a loading placeholder (the app's skeletons: .animate-pulse blocks),
 *  anything marked aria-busy (a list being refreshed), the Investments page's badge before the live prices arrive (it
 *  reads "Holdings updated …" until then, "Market closed · latest prices as of …" after), a transition still moving,
 *  fonts or the document still loading. */
export function unsettled(hidden = "[data-sonner-toaster], .just-saved, [style*=saved-fade]") {
  const doc = globalThis.document;
  const shown = (el) => el.getClientRects().length > 0 && globalThis.getComputedStyle(el).visibility !== "hidden";
  const name = (el) => {
    const label = el.getAttribute("aria-label"), cls = typeof el.className === "string" ? el.className.trim().split(/\s+/).slice(0, 4).join(".") : "";
    return `${el.tagName.toLowerCase()}${label ? `[aria-label="${label}"]` : ""}${cls ? `.${cls}` : ""}`;
  };
  const out = [];
  if (doc.readyState !== "complete") out.push("the document is still loading");
  if (doc.fonts && doc.fonts.status !== "loaded") out.push("fonts are still loading");
  // A placeholder is a block (10px or more each way); a pulsing status dot ("Not synced yet", 6px) is the page as it is.
  const block = (el) => { const r = el.getBoundingClientRect(); return r.width >= 10 && r.height >= 10; };
  const pulse = [...doc.querySelectorAll(".animate-pulse")].find((el) => shown(el) && block(el));
  if (pulse) out.push(`a loading placeholder is showing (${name(pulse)})`);
  const busy = [...doc.querySelectorAll("[aria-busy=true]")].find(shown);
  if (busy) out.push(`still busy (aria-busy on ${name(busy)})`);
  // A transition or animation that ends (a sliding pill, a panel opening), still moving: caught part way, the image depends
  // on the machine's speed. The endless ones (a pulsing dot) never end, and STILL stops them for the capture; what the
  // images leave out (`hidden`, HIDE_TOASTS's: a toast, the "Saved ✓" that fades after each save) doesn't count.
  const moving = (doc.getAnimations?.() ?? []).filter((a) => a.playState === "running" && a.effect?.getComputedTiming?.().endTime !== Infinity
    && !a.effect?.target?.closest?.(hidden));
  if (moving.length) out.push(`${moving.length} animation${moving.length === 1 ? " is" : "s are"} still running (${moving[0].animationName ?? moving[0].transitionProperty ?? "a transition"})`);
  const prices = doc.querySelector("[data-testid=holdings-updated]");
  if (prices && /^\s*Holdings updated/.test(prices.textContent ?? "")) out.push(`the live prices haven't arrived ("${prices.textContent.trim()}")`);
  return out;
}

/** Run in each page before the app (an init script). Chromium takes a full-page screenshot by resizing the window for a
 *  moment (to 1×1, then the whole page, then back), and the app reacts to that: at 1px wide its phone query matches,
 *  so a desktop page switched to the phone layout and back mid-capture, which closed an open picker, collapsed an
 *  expanded row and dropped the focus, before or after the pixels were taken depending on the machine's speed (the
 *  card-projection-open and transactions-category-picker images came out either way). While `__verifyCapturing` is
 *  set (capturing()), the app hears no media query change and no resize; after, one resize, at the real size. */
export function holdLayout() {
  const mql = globalThis.MediaQueryList?.prototype;
  if (!mql || mql.__verifyHeld) return;
  mql.__verifyHeld = true;
  const add = mql.addEventListener, remove = mql.removeEventListener, wrapped = new WeakMap();
  mql.addEventListener = function (type, fn, options) {
    if (type !== "change" || typeof fn !== "function") return add.call(this, type, fn, options);
    if (!wrapped.has(fn)) wrapped.set(fn, function (e) { if (!globalThis.__verifyCapturing) return fn.call(this, e); });
    return add.call(this, type, wrapped.get(fn), options);
  };
  mql.removeEventListener = function (type, fn, options) { return remove.call(this, type, wrapped.get(fn) ?? fn, options); };
  globalThis.addEventListener("resize", (e) => { if (globalThis.__verifyCapturing) e.stopImmediatePropagation(); }, true);
  // Elements resize with the window (a chart redrawn at 1px wide): their observers hear the size they're back at after.
  const Observer = globalThis.ResizeObserver;
  if (Observer) {
    globalThis.ResizeObserver = class extends Observer {
      constructor(callback) { super((entries, observer) => { if (!globalThis.__verifyCapturing) callback(entries, observer); }); }
    };
  }
}

/** Run in the page around a capture (holdLayout). Ending it waits two frames first, so the change events the capture's
 *  resizing queued are delivered (and dropped) while it's still held, then tells the app the window's real size once. */
export async function capturing(on) {
  if (on) { globalThis.__verifyCapturing = true; return; }
  await new Promise((r) => globalThis.requestAnimationFrame(() => globalThis.requestAnimationFrame(r)));
  globalThis.__verifyCapturing = false;
  globalThis.dispatchEvent(new Event("resize"));
}

/** How many times longer than on an idle machine a wait may take: what runway/verify.py asked for (`base`, the load it
 *  started at times the attempt), or the load now if that's more (per CPU, at most 8, times the attempt). Only the
 *  limits stretch: every wait still ends as soon as what it waits for is there. */
export function timeScale(base = 1, attempt = 1, load = loadavg()[0], cores = cpus().length) {
  return Math.max(base, Math.min(8, Math.max(1, load / Math.max(1, cores))) * attempt);
}

/** The device cache check's item (it isn't a page or a flow, so its name can't be one). */
export const DEVICE_CACHE = "device-cache";

/** The items a run covers: every page, flow and the device cache check, or, given names, those pages (with the flows
 *  that start on them; Transactions also brings the device cache check), flows by name and the device cache check by
 *  its. `only` (ids, from a retry) narrows that. Returns { items } or { unknown } (the names that are none of those). */
export function selectItems(names, flows, only) {
  const flowNames = new Set(flows.map((f) => f.name));
  const unknown = names.filter((n) => !PAGES.includes(n) && !flowNames.has(n) && n !== DEVICE_CACHE);
  if (unknown.length) return { unknown };
  const all = !names.length, items = [];
  for (const p of PAGES) if (all || names.includes(p)) items.push({ id: `page:${p}`, page: p });
  for (const f of flows) if (all || names.includes(f.name) || names.includes(f.page ?? "overview")) items.push({ id: `flow:${f.name}`, flow: f });
  if (all || names.includes("transactions") || names.includes(DEVICE_CACHE)) items.push({ id: DEVICE_CACHE });
  return { items: only ? items.filter((i) => only.includes(i.id)) : items };
}

/** Whether an item runs signed in (on the signed-in demo server): a flow with "signed_in": true, and the device cache check. */
export const signedInItem = (item) => (item.flow ? !!item.flow.signed_in : !item.page);

// A flow's steps that check what's on screen, whose check is made again after a screenshot taken since the last action:
// a state that falls apart while it's captured (a panel that closes again) fails the screenshot instead of being kept.
const CHECKS = ["expect_text", "expect_focus"];
const ACTS = ["goto", "click", "fill", "press", "tab", "reload", "scroll_to", "authenticator"];

/** A flow is { name, page?, inherits_page_baseline?, viewports?, signed_in?, steps: [ { goto | click | fill | press | wait_for | scroll_to | expect_text | expect_above | tab | expect_focus | expect_inert | screenshot | reload | authenticator } ] }:
 *  see frontend/verify/flows/README.md. Returns the problems with it, [] when it's well formed. */
const ACTIONS = { goto: "string", click: "string", fill: "object", press: "object", wait_for: "string", scroll_to: "string", expect_text: "object", expect_above: "object", screenshot: "string", tab: "object", expect_focus: "string", expect_inert: "string",
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
  out.push(...inheritProblems(flow, PAGES, DEFAULT_VIEWPORTS));
  // A flow's name is how a run picks it (`make verify PAGES=budget-edit`), so it can't also be a page's.
  if (typeof flow?.name === "string" && (PAGES.includes(flow.name) || flow.name === DEVICE_CACHE)) out.push(`the name ${flow.name} is taken (a page, or the device cache check)`);
  for (const v of flow?.viewports ?? []) if (!(v in VIEWPORTS) && !(v in LANDSCAPE)) out.push(`unknown viewport ${v}`);
  return out;
}

/** What keeps a click from landing, [] when nothing does; run in the page, so it stands alone. A modal dialog (a sheet, a
 *  confirm) that was just closed still holds the page through its closing animation and a moment after (bits-ui's scroll
 *  lock leaves the body at pointer-events: none until then), so a click on the page behind it hits the closing dialog or
 *  nothing. Playwright retries such a click, scrolling the target into view a different way on each try (end, centre,
 *  start), so where the page ended up scrolled depended on how many tries the closing took: budget-edit's phone images
 *  came out at three scroll positions, by how long after Cancel its next click started. */
export function heldByClosingModal() {
  const doc = globalThis.document;
  const open = doc.querySelector("[role=dialog][data-state=open], [role=alertdialog][data-state=open]");
  return !open && globalThis.getComputedStyle(doc.body).pointerEvents === "none" ? ["a dialog is still closing (the page takes no clicks yet)"] : [];
}

/** Waits until `check` (unsettled(), unless another is given) finds nothing, up to `timeout` ms; returns what was still
 *  loading then ([] when settled). */
// (Polled from here with page.evaluate: page.waitForFunction would eval a string in the page, which the app's CSP refuses.)
export async function waitSettled(page, timeout, check = unsettled) {
  const end = Date.now() + timeout;
  for (;;) {
    const left = await page.evaluate(check).catch((e) => [`the page couldn't be read (${String(e.message).split("\n")[0]})`]);
    if (!left.length || Date.now() > end) return left;
    await new Promise((r) => setTimeout(r, 100));
  }
}

/** Runs one flow step on `page` (which carries `t(ms)`, the stretched limit, and the flow's `checks` so far); `shot(name)`
 *  takes a screenshot step's image. Throws when the step fails. (control.mjs's `flow` runs flows with it too.) */
export async function runStep(page, step, shot) {
  const timeout = page.t(step.timeout ?? 10000);
  // A scroll_to sets where the page is scrolled for the shots after it, until a goto or reload moves to another page.
  if ("scroll_to" in step) page.scrolledByFlow = true;
  else if ("goto" in step || "reload" in step) page.scrolledByFlow = false;
  // The checks since the last action, made again after a screenshot (CHECKS).
  const action = Object.keys(step).find((k) => k !== "timeout");
  if (ACTS.includes(action)) page.checks = [];
  // A click waits for a dialog closed by the step before to let go of the page (heldByClosingModal; best effort: the click's
  // own retries still apply).
  if ("click" in step) await waitSettled(page, page.t(5000), heldByClosingModal);
  await doStep(page, step, shot, timeout);
  if (CHECKS.includes(action)) page.checks.push(step);
  // What a click, a fill or a key press sets off (a fetch for a row's details) has landed before the next step looks at the
  // page, so a step that picks the first match picks the same one every run.
  if ("click" in step || "fill" in step || "press" in step) await page.waitForLoadState("networkidle", { timeout: page.t(5000) }).catch(() => { /* a page that keeps a request open: the next step's own wait applies */ });
  // A new page (or the app opened again) has loaded before the next step picks something on it: a step that ran while the
  // list was still a placeholder, or before a refresh landed, acted on a page that then changed under it.
  if ("goto" in step || "reload" in step) await waitSettled(page, page.t(15000));   // (best effort: a screenshot insists)
}

/** Checks a flow's CHECKS steps again (after a screenshot); the first that no longer holds, or null. (control.mjs's flows too.) */
export async function recheck(page) {
  for (const step of page.checks ?? []) {
    try {
      await doStep(page, step, null, page.t(2000));
    } catch (e) {
      return `${JSON.stringify(step)} no longer held after the screenshot (${String(e.message).split("\n")[0]})`;
    }
  }
  return null;
}

async function doStep(page, step, shot, timeout) {
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
  } else if ("expect_above" in step) {   // the first match is whole inside the window and ends above the other (the floating navigation)
    const { selector, above } = step.expect_above;
    await page.locator(selector).first().waitFor({ timeout });
    const box = await page.locator(selector).first().boundingBox(), limit = await page.locator(above).first().boundingBox();
    const height = page.viewportSize()?.height ?? 0;
    if (!box || !limit || box.y < 0 || box.y + box.height > Math.min(height, limit.y)) {
      throw new Error(`expected ${selector} (${box ? `${Math.round(box.y)}–${Math.round(box.y + box.height)}` : "not shown"}) above ${above} (${limit ? Math.round(limit.y) : "not shown"}) on the first screen`);
    }
  } else if ("tab" in step) {   // the real keyboard: Tab (or Shift+Tab) `count` times, each landing inside `stay_in`
    const { count = 1, shift = false, stay_in: within } = step.tab;
    for (let i = 1; i <= count; i++) {
      await page.keyboard.press(shift ? "Shift+Tab" : "Tab");
      if (!within) continue;
      const inside = await page.evaluate((sel) => !!globalThis.document.activeElement?.closest(sel), within);
      if (!inside) throw new Error(`${shift ? "Shift+Tab" : "Tab"} ${i} left ${within}, onto ${await page.evaluate(() => globalThis.document.activeElement?.outerHTML.slice(0, 80))}`);
    }
  } else if ("expect_focus" in step) {   // the focused element matches the selector
    await page.waitForFunction((sel) => !!globalThis.document.activeElement?.matches(sel), step.expect_focus, { timeout })
      .catch(async () => { throw new Error(`expected focus on ${step.expect_focus}, found ${await page.evaluate(() => globalThis.document.activeElement?.outerHTML.slice(0, 80))}`); });
  } else if ("expect_inert" in step) {   // the element, or something around it, is inert
    const inert = await page.locator(step.expect_inert).first().evaluate((el) => !!el.closest("[inert]"), undefined, { timeout });
    if (!inert) throw new Error(`expected ${step.expect_inert} to be inert`);
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
export async function signIn(ctx, page, url, token, { prf = false } = {}) {
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
 *  72-hour window it isn't painted (it's wiped at launch); signing out leaves nothing in IndexedDB. It signs the session
 *  out at the end: on a server and data of its own, like every item, so no flow is left signed out. */
async function cacheCheck(page, base) {
  const HOLD_MS = page.t(60000);
  // The lock screen offers the unlock on its own; should the browser want a tap first, the button.
  const launch = async () => {
    await page.reload({ waitUntil: "load" });
    void page.locator("button:text-is('Unlock'):not([disabled])").click({ timeout: page.t(8000) }).catch(() => { /* unlocked on its own */ });
  };
  if (!page.frozen) await page.clock.install();   // (time runs as usual; jumped ahead below; a frozen run installed it at the context)
  await page.goto(`${base}/#setup/data`, { waitUntil: "networkidle" });
  await page.locator("button:text-is('Turn on app lock')").first().click();
  await page.locator("text=On for this device").first().waitFor();
  // A launch: the lock screen, and the unlock it offers on its own (the authenticator verifies).
  await page.goto(`${base}/#transactions`);
  await launch();
  await page.locator("[data-testid=tx-rows][aria-busy=false] [data-tx-list]").waitFor({ timeout: page.t(20000) });
  await until(async () => (await cacheRows(page))?.some((r) => r.k.startsWith("r:transactions?")), "the Transactions list kept on the device", page.t(10000));
  const rows = await cacheRows(page);
  if (!rows.some((r) => r.k === "share")) throw new Error("no sealed copy of the share on the device");
  const words = [...new Set((await page.locator("[data-tx-list]").innerText()).split(/\s+/).filter((w) => /^[A-Za-z]{5,}$/.test(w)))].slice(0, 20);
  const stored = JSON.stringify(rows);
  const readable = words.filter((w) => stored.includes(w));
  if (!words.length || readable.length) throw new Error(`the cache isn't sealed: ${readable.join(", ") || "no words to look for"}`);
  for (const r of rows) for (const f of Object.keys(r)) if (!["k", "v", "version", "at", "iv", "ct", "device", "fetchedAt", "expiresAt"].includes(f)) throw new Error(`unexpected field ${f} in a stored row`);

  // Cold start, the server slow to send the list: painted from the device (marked as not yet current), then replaced.
  // The server's list is held until the check has seen what it looks for (not for a fixed time, which a slow machine
  // outlasts: the list then arrived first and the check failed), or HOLD_MS at most.
  let held = 0, released = 0, open = () => {};
  let gate = new Promise((r) => { open = r; });
  await page.route(/\/api\/transactions\?/, async (route) => {
    held++;
    await Promise.race([gate, new Promise((r) => setTimeout(r, HOLD_MS))]);
    released++;
    await route.continue().catch(() => { /* the page moved on */ });
  });
  await launch();
  await page.locator("[data-testid=tx-rows][aria-busy=true] [data-tx-list]").waitFor({ timeout: page.t(20000) });
  if (!held || released) throw new Error("the list wasn't painted from the device before the server's arrived");
  open();
  await page.locator("[data-testid=tx-rows][aria-busy=false] [data-tx-list]").waitFor({ timeout: page.t(20000) });

  // Past the window: the copy of the share is out of date at launch, so the cache goes, and nothing is painted from it.
  await page.clock.setSystemTime(Date.now() + 73 * 3600 * 1000);
  gate = new Promise((r) => { open = r; });
  await launch();
  // Either the page's loading placeholder (from the server: right) or rows painted from the device (wrong) comes first.
  await page.locator("[role=status][aria-busy=true], [data-testid=tx-rows][aria-busy=true]").first().waitFor({ timeout: page.t(20000) });
  if (await page.locator("[data-testid=tx-rows][aria-busy=true]").count()) throw new Error("painted from a cache past its window");
  open();
  await page.locator("[data-testid=tx-rows][aria-busy=false] [data-tx-list]").waitFor({ timeout: page.t(20000) });
  await page.unroute(/\/api\/transactions\?/);

  // Signing out deletes it.
  await page.locator("a[aria-label='Sign out']").click();
  await page.waitForURL((u) => !u.hash.includes("transactions"), { timeout: page.t(15000) }).catch(() => { /* the page may already have moved on: the rows left are checked below */ });
  await page.goto(`${base}/auth/signed-out`).catch(() => { /* signing out may navigate meanwhile: the rows left are checked below */ });
  const left = await cacheRows(page);
  if (left && left.length) throw new Error(`signing out left ${left.length} row(s) in IndexedDB`);
}

/** A new browser context made ready the way every visit is (control.mjs's session too): the browser's clock at the frozen
 *  `day` (fixed, or running from it when the visit `moves` it itself), the app's minute timers off (unless it `moves`),
 *  PAGE_STYLE in every page from the start and holdLayout. */
export async function prepareContext(ctx, { day, moves = false } = {}) {
  // The device cache check moves the clock itself (setSystemTime), so it gets a running one; every other visit's clock is
  // fixed, so a time of day ("as of 12:00:18 PM") doesn't change from one run to the next.
  if (day) await (moves ? ctx.clock.install({ time: frozenTime(day) }) : ctx.clock.setFixedTime(frozenTime(day)));
  // PAGE_STYLE in the page from the start (the capture also stops the endless animations, STILL).
  // Nor the app's check-in every minute (setInterval(checkIn, 60_000) in lib/app.svelte.ts): its refresh re-renders the lists, which
  // drops an open confirm dialog or an expanded row wherever in a flow it lands (a guess at the transactions-delete flake, and the card
  // projection that's sometimes collapsed in its shot). The device cache check keeps it: it tests the app as it runs.
  if (!moves) await ctx.addInitScript(() => {
    const real = globalThis.setInterval.bind(globalThis);
    globalThis.setInterval = (fn, ms, ...rest) => (ms >= 30000 ? 0 : real(fn, ms, ...rest));
  });
  await ctx.addInitScript((css) => {
    const style = globalThis.document.createElement("style");
    style.textContent = css;
    globalThis.document.addEventListener("DOMContentLoaded", () => globalThis.document.head.append(style));
  }, PAGE_STYLE);
  await ctx.addInitScript(holdLayout);
}

/** The flows in `dir` (each file's JSON, checked with flowProblems), sorted by file name: { flows } or { error }. */
export function readFlows(dir) {
  if (!existsSync(dir)) return { flows: [] };
  const flows = [];
  for (const f of readdirSync(dir).filter((x) => x.endsWith(".json")).sort()) {
    let flow;
    try { flow = JSON.parse(readFileSync(join(dir, f), "utf8")); } catch (e) { return { error: `flow ${f} isn't valid JSON (${e.message})` }; }
    const bad = flowProblems(flow);
    if (bad.length) return { error: `flow ${f}: ${bad.join("; ")}` };
    flows.push(flow);
  }
  return { flows };
}

// A screenshot is retaken when the page wasn't settled (SHOT_TRIES in all) and fails after that: never kept half loaded.
const SHOT_TRIES = 4;

async function main() {
  const args = process.argv.slice(2);
  const opt = (name, fallback) => { const i = args.indexOf(name); return i < 0 ? fallback : args.splice(i, 2)[1]; };
  const base = opt("--url", process.env.RUNWAY_VERIFY_URL);
  const here = dirname(fileURLToPath(import.meta.url));
  const out = opt("--out", join(here, "../../artifacts/verify"));
  const flowsDir = opt("--flows", join(here, "flows"));
  // The signed-in demo session's token (runway/verify.py), for flows with "signed_in": true and the device cache check.
  const session = process.env.RUNWAY_VERIFY_SESSION;
  // The day runway/verify.py froze the server and the demo seed at (libfaketime): the browser's clock is set to it too, so
  // the dates a page shows, and so its image, don't depend on the day it was taken. Run alone, with no --day, the clock is real.
  const day = opt("--day", process.env.RUNWAY_VERIFY_DAY);
  // How much to stretch every wait's limit (runway/verify.py: the machine's load, times the attempt), and which attempt.
  const baseScale = Number(opt("--time-scale", "1")), attempt = Number(opt("--attempt", "1"));
  const only = opt("--only", undefined)?.split(",").filter(Boolean);
  // A server of its own for each item (runway/verify.py): the seeded data each one copies, the folder the copies go in and
  // the Python that runs `serve`; how many items run at once, and whether the queue runs backwards (make verify-twice's second run).
  const seed = opt("--seed", undefined), workDir = opt("--work", undefined), python = opt("--python", undefined);
  const jobs = Number(opt("--jobs", "1"));
  const reverse = args.includes("--reverse") && !!args.splice(args.indexOf("--reverse"), 1);
  if (day !== undefined && !/^\d{4}-\d{2}-\d{2}$/.test(day)) { console.error(`verify: --day is YYYY-MM-DD, not ${day}`); process.exit(2); }
  if (!(baseScale >= 1) || !(attempt >= 1)) { console.error("verify: --time-scale and --attempt are numbers, at least 1"); process.exit(2); }
  if (!Number.isInteger(jobs) || jobs < 1) { console.error("verify: --jobs is a whole number, at least 1"); process.exit(2); }
  if (seed && !(workDir && python)) { console.error("verify: --seed needs --work and --python"); process.exit(2); }
  if (!base && !seed) { console.error("verify: no server (--seed, or the address of one already running: --url or RUNWAY_VERIFY_URL)"); process.exit(2); }

  const { flows, error } = readFlows(flowsDir);
  if (error) { console.error(`verify: ${error}`); process.exit(2); }
  const { items, unknown } = selectItems(args, flows, only);
  if (unknown) {
    console.error(`verify: no such page or flow: ${unknown.join(", ")} (pages: ${PAGES.join(", ")}; flows: the names in ${flowsDir}; ${DEVICE_CACHE})`);
    process.exit(2);
  }

  const a11yBaseline = readBaseline();
  if (a11yBaseline.errors.length) { console.error(`verify: ${a11yBaseline.errors.join("; ")}`); process.exit(2); }

  rmSync(out, { recursive: true, force: true });
  mkdirSync(out, { recursive: true });
  // Rewritten after each item, so a run that dies part way says which items it never reached.
  // Every item there is (not only this run's), so tools/sync_screenshots.py can tell which item an image belongs to.
  const report = { base: seed ? "a server of its own for each item" : base, attempt, jobs: seed ? jobs : 1, all_items: selectItems([], flows).items.map(({ id }) => id), items: items.map(({ id }) => ({ id, ok: false, files: [], failed_files: [], problems: ["not run (the run ended first)"], notes: [] })) };
  const save = () => writeFileSync(join(out, "report.json"), JSON.stringify(report, null, 2));
  save();

  async function visit(browser, urls, result, label, viewport, work, signedIn = false, authenticator = {}, inheritFrom = undefined) {
    const ctx = await browser.newContext(viewport in LANDSCAPE ? { viewport: LANDSCAPE[viewport], hasTouch: true } : { viewport: VIEWPORTS[viewport] });
    const moves = label === DEVICE_CACHE;
    await prepareContext(ctx, { day, moves });
    const page = await ctx.newPage();
    // Every limit stretched by the load (each wait still ends as soon as what it waits for is there).
    const scale = timeScale(baseScale, attempt);
    page.t = (ms) => Math.round(ms * scale);
    page.setDefaultTimeout(page.t(30000));
    page.setDefaultNavigationTimeout(page.t(30000));
    page.frozen = !!day && moves;
    if (signedIn) await signIn(ctx, page, urls.signedIn, session, authenticator);
    const where = `${label} @ ${viewport}`;
    const consoleErrors = [];
    page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text()); });
    page.on("pageerror", (e) => consoleErrors.push(`uncaught: ${e.message}`));
    page.on("response", (r) => { if (r.status() >= 500) result.problems.push(`${where}: ${r.status()} from ${r.request().method()} ${new URL(r.url()).pathname}`); });
    page.on("requestfailed", (r) => { if (r.failure()?.errorText !== "net::ERR_ABORTED") result.notes.push(`${where}: request failed (${r.failure()?.errorText}) ${new URL(r.url()).pathname}`); });
    // A screenshot is taken only once the page has loaded (unsettled() finds nothing) and stopped changing (settle()), then
    // taken again at once: kept when the two are byte for byte the same and nothing started loading meanwhile, retaken
    // otherwise, and a failure after SHOT_TRIES. A flow's checks since its last action must still hold after it (CHECKS).
    // `top`: the window as it opens, not the full page (TOP).
    const shot = async (name, { top = false } = {}) => {
      const file = screenshotFile(name, viewport);
      let why = "";
      for (let i = 1; i <= SHOT_TRIES; i++) {
        await page.waitForLoadState("networkidle", { timeout: page.t(10000) }).catch(() => { /* a page that keeps a request open: the readiness check below still applies */ });
        const loading = await waitSettled(page, page.t(20000));
        if (loading.length) { why = `still loading: ${loading.join("; ")}`; continue; }
        await page.mouse.move(0, 0);   // no row under the pointer: a hovered row shows its checkbox and buttons
        await page.evaluate(settle, !!page.scrolledByFlow);
        // A modal (a confirm dialog, a sheet) is shot as the window shows it, not the whole page: a full-page capture resizes the
        // window, and that sometimes made the dialog lose focus and close before the flow's next step (the transactions-delete flake).
        // The To review bar is the same: a full-page capture makes the whole card visible again, and the bar goes with it.
        const modal = await page.locator("[role=dialog]:visible, [role=alertdialog]:visible, [aria-modal=true]:visible, [data-review-bar]:visible").count();
        const options = captureOptions({ modal: modal > 0, top, timeout: page.t(30000) });
        await page.evaluate(capturing, true);
        const first = await page.screenshot(options), second = await page.screenshot(options);
        await page.evaluate(capturing, false);
        const after = await page.evaluate(unsettled);
        if (after.length) { why = `started loading again: ${after.join("; ")}`; continue; }
        if (!first.equals(second)) { why = "still changing (two captures in a row differed)"; continue; }
        const lost = await recheck(page);
        if (lost) throw new Error(lost);
        writeFileSync(join(out, file), first);
        result.files.push(file);
        if (i > 1) result.notes.push(`${where}: ${file} taken on try ${i} (before that: ${why})`);
        return;
      }
      throw new Error(`${file} never settled (${SHOT_TRIES} tries): ${why}`);
    };
    try {
      await work(page, shot);
      // Every page and flow, at the end of its steps (a11y.mjs): axe, tap targets on the phone, a focus ring on each tab stop.
      if (label !== DEVICE_CACHE) {
        const { found, overlay } = await auditVisit(page, viewport);
        result.problems.push(...visitProblems(found, where, a11yBaseline.entries, inheritFrom, overlay));
      }
    } catch (e) {
      result.problems.push(`${where}: ${String(e.message).split("\n")[0]}`);
      console.error(`${where}: ${e.message}`);   // the whole message, with Playwright's call log, for finding why
      // Straight to page.screenshot, not shot(): a failed step keeps its evidence in artifacts/verify/ (sync_screenshots never commits -FAILED- files).
      const failed = screenshotFile(`${label.replace(/\W+/g, "-")}-FAILED`, viewport);
      await page.screenshot({ path: join(out, failed), fullPage: true, timeout: page.t(30000) }).then(() => result.failed_files.push(failed))
        .catch(() => { /* best effort: the step's own error is already recorded above */ });
    }
    for (const c of consoleErrors) result.problems.push(`${where}: console error: ${c.slice(0, 300)}`);
    await ctx.close();
  }

  /** One item's visits (each width a new browser context), against `urls`: { base, signedIn } (the one that applies to it). */
  async function runItem(browser, urls, item, result) {
    if (item.page) {
      for (const viewport of DEFAULT_VIEWPORTS) {
        await visit(browser, urls, result, item.page, viewport, async (page, shot) => {
          await page.goto(`${urls.base}/#${item.page}`, { waitUntil: "networkidle" });
          await shot(item.page);
          await shot(`${item.page}-${TOP}`, { top: true });
        });
      }
    } else if (item.flow) {
      const flow = item.flow;
      for (const viewport of flow.viewports ?? DEFAULT_VIEWPORTS) {
        await visit(browser, urls, result, `flow ${flow.name}`, viewport, async (page, shot) => {
          await page.goto(`${flow.signed_in ? urls.signedIn : urls.base}/#${flow.page ?? "overview"}`, { waitUntil: "networkidle" });
          await waitSettled(page, page.t(15000));   // loaded before the first step picks something on it (a screenshot insists)
          page.checks = [];
          for (const step of flow.steps) await runStep(page, step, (n) => shot(`flow-${flow.name}-${n}`));
          await shot(`flow-${flow.name}`);
        }, !!flow.signed_in, {}, inheritedWhere(flow, viewport, PAGES, DEFAULT_VIEWPORTS));
      }
    } else {
      await visit(browser, urls, result, DEVICE_CACHE, "desktop", (page) => cacheCheck(page, urls.signedIn), true, { prf: true });
    }
  }

  const scale = timeScale(baseScale, attempt);
  let started = 0;   // each server's folder is numbered, so no two are ever the same

  /** Runs one item on a worker's browser: on a server and a copy of the data of its own (--seed), started for it and
   *  stopped after, or on the one server it was given (--url). Its result goes into the report, which is saved. */
  async function runOne(item, browser) {
    const result = report.items.find((r) => r.id === item.id);
    result.problems = [];
    const signedIn = signedInItem(item);
    if (signedIn && !session) result.notes.push(`${item.id}: skipped (no signed-in session: run it with make verify)`);
    else if (signedIn && !seed) result.notes.push(`${item.id}: skipped (it needs a signed-in server of its own: run it with make verify)`);
    else if (!seed) await runItem(browser, { base }, item, result);
    else {
      let server;
      try {
        server = await startServer({
          command: (port, dir) => [python, "-m", "runway.verify", "serve", "--parent", String(process.pid), "--seed", seed, "--data", dir, "--port", String(port), ...(signedIn ? ["--signed-in"] : [])],
          folder: join(workDir, `server-${++started}`), timeout: 60000 * scale, cwd: join(here, "../.."),
        });
      } catch (e) {
        result.problems.push(`${item.id}: ${String(e.message).split("\n")[0]}`);
        console.error(`${item.id}: ${e.message}`);   // with the end of the server's log
      }
      if (server) {
        // The signed-in server is reached at localhost (its sign-in's public address), the other at the loopback address.
        const url = `http://${signedIn ? "localhost" : "127.0.0.1"}:${server.port}`;
        try {
          await runItem(browser, signedIn ? { signedIn: url } : { base: url }, item, result);
        } finally {
          await server.stop();
        }
      }
    }
    result.ok = !result.problems.length;
    save();
    console.log(`  ${result.ok ? "ok  " : "FAIL"} ${item.id}${result.files.length ? ` (${result.files.length} image${result.files.length === 1 ? "" : "s"})` : ""}`);
    for (const n of result.notes) console.log(`       note: ${n}`);
    for (const p of result.problems) console.error(`       ${p}`);
    return {};
  }

  // Each worker is a browser of its own (launchOptions). With --url, one item at a time: they share that server.
  const { outcomes, failures } = await runPool({
    items, workers: seed ? jobs : 1, reverse,
    prepare: () => chromium.launch(launchOptions()),
    work: runOne,
    release: (browser) => browser.close(),
  });
  for (const f of failures) console.error(`verify: worker ${f.worker + 1} ${f.reason}`);
  // What never ran, or what a worker stopped on (its browser gone): failed, saying why.
  for (const o of outcomes.filter((x) => x.worker === null || x.problems?.length)) {
    const result = report.items.find((r) => r.id === o.item.id);
    result.problems = [...result.problems.filter((p) => !p.startsWith("not run")), ...o.problems];
    result.ok = false;
    console.error(`  FAIL ${o.item.id}: ${o.problems[0]}`);
  }
  save();

  const failed = report.items.filter((r) => !r.ok);
  const shots = report.items.reduce((n, r) => n + r.files.length, 0);
  console.log(`verify: attempt ${attempt}: ${report.items.length} pages and flows, ${shots} screenshots in ${out}` +
    `${seed && jobs > 1 ? `, ${jobs} at a time` : ""}; ${failed.length ? `FAILED: ${failed.map((r) => r.id).join(", ")}` : "all passed"}`);
  process.exit(failed.length ? 1 : 0);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) main().catch((e) => { console.error(e); process.exit(2); });
