// The control session (control.mjs's `start` launches it in the background): a demo server on a fresh copy of the demo
// data under the frozen clock (runway/verify.py's `seed` and `serve`, as make verify runs them) and a Chromium on it, made
// ready the way make verify's browsers are (verify.mjs's prepareContext). It listens on a Unix socket (artifacts/control/,
// this user only) and runs the commands sent to it one at a time, each request carrying the token from the state file.
// It ends on `stop`, on a signal, when its browser closes, or after its idle minutes; its demo server ends with it
// (serve's die_with_parent) and it removes its data, its socket and the state file on the way out.
//
//   node verify/control-daemon.mjs --dir <artifacts/control> --python <python> --day <YYYY-MM-DD> --viewport phone
//        --idle-minutes 30 --seed ai-buttons,investments [--signed-in]
import { chromium } from "@playwright/test";
import { spawn } from "node:child_process";
import { randomBytes, timingSafeEqual } from "node:crypto";
import { chmodSync, existsSync, mkdirSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:net";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { ARTIFACTS, ControlError, PROG, ROOT, closest, errorResult, jsExpression, lastShotNumber, parseTarget, readState, rel, removeState, routeUrl, scrubText, scrubUrl, shotName, socketPath, writeState } from "./control.mjs";
import { startServer } from "./jobs.mjs";
import { VIEWPORTS, captureOptions, capturing, launchOptions, prepareContext, readFlows, runStep, screenshotFile, settle, signIn, timeScale, unsettled, waitSettled } from "./verify.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const MODAL = "[role=dialog]:visible, [role=alertdialog]:visible, [aria-modal=true]:visible, [data-review-bar]:visible";
const SHOT_TRIES = 4;
const BUFFER = 500;   // console messages and requests kept between two reads (the oldest dropped first)

const log = (line) => console.log(`${new Date().toISOString()} ${line}`);
const firstLine = (e) => String(e?.message ?? e).split("\n")[0];

function options(argv) {
  const get = (name) => { const i = argv.indexOf(`--${name}`); return i < 0 ? undefined : argv[i + 1]; };
  return { dir: get("dir"), python: get("python"), day: get("day"), viewport: get("viewport") ?? "phone", idleMinutes: Number(get("idle-minutes") ?? 30),
    seed: (get("seed") ?? "").split(",").filter(Boolean), signedIn: argv.includes("--signed-in") };
}

/** Runs a program to its end; rejects with the end of its output when it fails. */
function runToEnd(program, args, env) {
  return new Promise((resolve, reject) => {
    const proc = spawn(program, args, { cwd: ROOT, env, stdio: ["ignore", "pipe", "pipe"] });
    let out = "";
    proc.stdout.on("data", (d) => { out += d; });
    proc.stderr.on("data", (d) => { out += d; });
    proc.on("error", reject);
    proc.on("exit", (code) => (code === 0 ? resolve() : reject(new Error(`${args.slice(0, 3).join(" ")} exited with ${code}: ${out.trim().split("\n").slice(-3).join(" | ")}`))));
  });
}

async function main() {
  const opt = options(process.argv.slice(2));
  if (!opt.dir || !opt.dir.startsWith(`${ARTIFACTS}/`) || !opt.python || !/^\d{4}-\d{2}-\d{2}$/.test(opt.day ?? "") || !(opt.viewport in VIEWPORTS)) {
    console.error("control-daemon: started without its options (tools/control start starts it)");
    process.exit(2);
  }
  const { dir } = opt;
  const sessionDir = join(dir, `session-${process.pid}`);
  const seedDir = join(sessionDir, "seed");
  const token = randomBytes(32).toString("hex");
  const sessionToken = opt.signedIn ? randomBytes(32).toString("base64url") : "";   // the signed-in browser's; never written down
  const socket = socketPath(dir);
  const scale = timeScale(1, 1);
  const state = { pid: process.pid, status: "starting", dir, sessionDir, socket, token, seed: opt.seed, signedIn: opt.signedIn, viewport: opt.viewport,
    idleMinutes: opt.idleMinutes, day: opt.day, startedAt: new Date().toISOString() };
  const save = (more = {}) => { Object.assign(state, more); writeState(state, dir); };
  save();

  let server = null, browser = null, listener = null, page = null, ctx = null, base = "", servers = 0, idle = null, ending = false;
  const consoleBuffer = [], requests = [], offsite = [];
  let shots = 0;

  async function shutdown(reason, code = 0) {
    if (ending) return;
    ending = true;
    log(`stopping: ${reason}`);
    clearTimeout(idle);
    if (listener) await new Promise((r) => listener.close(() => r())).catch(() => { /* already closed */ });
    rmSync(socket, { force: true });
    if (browser) await Promise.race([browser.close(), new Promise((r) => setTimeout(r, 10000))]).catch(() => { /* gone already */ });
    if (server) await server.stop().catch((e) => log(`stopping the demo server: ${firstLine(e)}`));
    rmSync(sessionDir, { recursive: true, force: true });
    if (readState(dir)?.pid === process.pid) removeState(dir);
    process.exit(code);
  }
  for (const sig of ["SIGTERM", "SIGINT", "SIGHUP"]) process.on(sig, () => shutdown(`signal ${sig}`));
  process.on("uncaughtException", (e) => { log(`uncaught: ${e.stack ?? e}`); shutdown("an internal error", 1); });
  process.on("unhandledRejection", (e) => { log(`unhandled: ${e?.stack ?? e}`); shutdown("an internal error", 1); });
  const touch = () => { clearTimeout(idle); idle = setTimeout(() => shutdown(`idle for ${opt.idleMinutes} minutes`), opt.idleMinutes * 60000); };

  async function newServer() {
    server = await startServer({
      command: (port, folder) => [opt.python, "-m", "runway.verify", "serve", "--parent", String(process.pid), "--seed", seedDir, "--data", folder, "--port", String(port), ...(opt.signedIn ? ["--signed-in"] : [])],
      folder: join(sessionDir, `data-${++servers}`), timeout: 60000 * scale, cwd: ROOT,
    });
    // The signed-in server is reached at localhost (its sign-in's public address), the other at the loopback address, as in make verify.
    base = `http://${opt.signedIn ? "localhost" : "127.0.0.1"}:${server.port}`;
    if (ctx && opt.signedIn) await ctx.addCookies([{ name: "runway_session", value: sessionToken, url: base }]);
  }

  try {
    mkdirSync(sessionDir, { recursive: true });
    log(`seeding the demo data (${opt.seed.join(", ") || "plain"}${opt.signedIn ? ", signed in" : ""}) under the frozen clock`);
    await runToEnd(opt.python, ["-m", "runway.verify", "seed", "--data", seedDir, ...opt.seed.map((s) => `--${s}`), ...(opt.signedIn ? ["--signed-in"] : [])],
      { ...process.env, RUNWAY_VERIFY_SESSION: sessionToken });
    await newServer();
    log(`demo server at ${base}`);
    browser = await chromium.launch(launchOptions());
    browser.on("disconnected", () => shutdown("the browser closed", 1));
    ctx = await browser.newContext({ viewport: VIEWPORTS[opt.viewport] });
    await prepareContext(ctx, { day: opt.day });
    page = await ctx.newPage();
    page.t = (ms) => Math.round(ms * scale);
    page.setDefaultTimeout(page.t(30000));
    page.setDefaultNavigationTimeout(page.t(30000));
    if (opt.signedIn) await signIn(ctx, page, base, sessionToken);
    // What console and network report (scrubbed: no query strings, amounts or ids).
    const keep = (list, entry) => { list.push(entry); if (list.length > BUFFER) list.shift(); };
    page.on("console", (m) => keep(consoleBuffer, { type: m.type(), text: scrubText(m.text()).slice(0, 300), route: route() }));
    page.on("pageerror", (e) => keep(consoleBuffer, { type: "pageerror", text: scrubText(firstLine(e)).slice(0, 300), route: route() }));
    page.on("response", (r) => { const path = scrubUrl(r.url(), base); if (path) keep(requests, { method: r.request().method(), path, status: r.status(), type: r.request().resourceType() }); });
    // net::ERR_ABORTED is a request the page dropped itself (it moved on, or a 304 it had already answered from its cache):
    // make verify ignores it, so it's reported as aborted, not failed.
    page.on("requestfailed", (r) => {
      const path = scrubUrl(r.url(), base), why = r.failure()?.errorText ?? "failed";
      if (path) keep(requests, { method: r.method(), path, ...(why === "net::ERR_ABORTED" ? { aborted: true } : { failed: why }), type: r.resourceType() });
    });
    // Only ever its own server: a page that navigates elsewhere is brought back (and the next command says so); popups are closed.
    page.on("framenavigated", (frame) => {
      if (frame !== page.mainFrame() || ending) return;
      let origin;
      try { origin = new URL(frame.url()).origin; } catch { return; /* about:blank and the like */ }
      if (origin !== "null" && origin !== new URL(base).origin) {
        offsite.push(origin);
        page.goto(`${base}/#overview`).catch((e) => log(`coming back from ${origin}: ${firstLine(e)}`));
      }
    });
    ctx.on("page", (p) => { if (p !== page) p.close().catch(() => { /* closed already */ }); });
    await page.goto(`${base}/#overview`, { waitUntil: "networkidle" });
    await waitSettled(page, page.t(15000));
  } catch (e) {
    log(`start failed: ${e.stack ?? e}`);
    save({ status: "failed", error: firstLine(e) });
    await shutdown("it failed to start", 1);
    return;
  }

  function route() {
    try { const u = new URL(page.url()); return `${u.pathname}${u.hash}`; } catch { return page.url(); }
  }

  /** The page's target, one match: { loc, label }. Errors say how to name it instead. */
  async function pick(words, { nth, exact = false, timeout = 10000 } = {}) {
    const target = parseTarget(words);
    const found = (target.kind === "role" ? page.getByRole(target.role, target.name ? { name: target.name, exact } : {})
      : target.kind === "text" ? page.getByText(target.text, { exact }) : page.locator(target.selector)).filter({ visible: true });
    const label = target.kind === "role" ? `${target.role}${target.name ? ` "${target.name}"` : ""}` : target.kind === "text" ? `text "${target.text}"` : target.selector;
    try {
      await found.first().waitFor({ state: "visible", timeout: page.t(timeout) });
    } catch (e) {
      if (/Timeout/.test(e.message)) throw new ControlError("no_match", `nothing visible matches ${label} on ${route()}`, `run \`${PROG} snapshot --text\` for the roles and names on the page`);
      throw bad(e);
    }
    const count = await found.count();
    if (nth !== undefined) {
      if (nth >= count) throw new ControlError("no_match", `--nth ${nth}, but only ${count} visible element${count === 1 ? " matches" : "s match"} ${label}`, `use --nth 0 to ${count - 1}`);
      return { loc: found.nth(nth), label: `${label} (match ${nth} of ${count})` };
    }
    if (count > 1) {
      const seen = [];
      for (let i = 0; i < Math.min(count, 6); i++) seen.push(`${i}: ${await describe(found.nth(i))}`);
      throw new ControlError("ambiguous", `${count} visible elements match ${label}: ${seen.join("; ")}${count > 6 ? "; …" : ""}`, `pass --nth <0-${count - 1}>, or name it more exactly (a role and its name, --exact)`);
    }
    return { loc: found.first(), label };
  }

  const describe = (loc) => loc.evaluate((el) => {
    const text = (el.getAttribute("aria-label") || el.textContent || el.getAttribute("placeholder") || "").trim().replace(/\s+/g, " ").slice(0, 50);
    return `${el.getAttribute("role") || el.tagName.toLowerCase()}${text ? ` "${text}"` : ""}`;
  }).catch(() => "(gone)");

  /** A Playwright failure as an error the agent can act on. */
  function bad(e) {
    if (e instanceof ControlError) return e;
    const m = firstLine(e);
    if (/ERR_CONNECTION_REFUSED|ECONNREFUSED/.test(m)) return new ControlError("server_gone", "the demo server isn't answering", `run \`${PROG} reset\` (fresh data on a new server), or \`${PROG} stop\` and \`${PROG} start\``);
    if (/Timeout \d+ms exceeded/.test(m)) return new ControlError("timeout", m.replace(/^\w+\.\w+: /, ""), `the page may still be loading (\`${PROG} wait-settle\`), or the target is covered or disabled (\`${PROG} screenshot\` to look)`);
    if (/Unknown key/.test(m)) return new ControlError("bad_key", m, "keys are Playwright's names: Enter, Escape, Tab, Shift+Tab, ArrowDown, Control+a");
    return new ControlError("failed", m, `\`${PROG} screenshot\` and \`${PROG} console\` show what the page did`);
  }

  /** A screenshot taken as make verify takes one (verify.mjs's visit: settled, two matching captures), written to `path`. */
  async function capture(path, { top = false, keepScroll = false } = {}) {
    let why = "";
    mkdirSync(dirname(path), { recursive: true });
    for (let i = 1; i <= SHOT_TRIES; i++) {
      await page.waitForLoadState("networkidle", { timeout: page.t(10000) }).catch(() => { /* a page that keeps a request open: the readiness check below still applies */ });
      const loading = await waitSettled(page, page.t(20000));
      if (loading.length) { why = `still loading: ${loading.join("; ")}`; continue; }
      await page.mouse.move(0, 0);   // no row under the pointer: a hovered row shows its checkbox and buttons
      await page.evaluate(settle, keepScroll);
      const modal = (await page.locator(MODAL).count()) > 0;
      const opts = captureOptions({ modal, top, timeout: page.t(30000) });
      await page.evaluate(capturing, true);
      let first, second;
      try { first = await page.screenshot(opts); second = await page.screenshot(opts); } finally { await page.evaluate(capturing, false); }
      const after = await page.evaluate(unsettled);
      if (after.length) { why = `started loading again: ${after.join("; ")}`; continue; }
      if (!first.equals(second)) { why = "still changing (two captures in a row differed)"; continue; }
      writeFileSync(path, first);
      return { path: rel(path), fullPage: opts.fullPage, modal, tries: i };
    }
    const evidence = path.replace(/\.png$/, "-UNSETTLED.png");
    await page.screenshot({ path: evidence, fullPage: !top }).catch(() => { /* best effort: the error below says why */ });
    throw new ControlError("unsettled", `the page never settled (${SHOT_TRIES} tries): ${why}`, `an image of it as it was is at ${rel(evidence)}; a page that never settles fails make verify too`);
  }

  async function info() {
    const size = page.viewportSize();
    return { route: route(), title: await page.title(), viewport: { name: state.viewport, ...size },
      clock: { frozenDay: opt.day, browser: await page.evaluate(() => new Date().toString()), server: `${opt.day} 12:00:00, stopped (libfaketime)` },
      session: { pid: process.pid, server: base, seed: opt.seed, signedIn: opt.signedIn, idleMinutes: opt.idleMinutes } };
  }

  const settledNow = async (ms) => { const left = await waitSettled(page, page.t(ms)); return left.length ? { settled: false, stillLoading: left } : { settled: true }; };
  const afterAction = async () => {
    await page.waitForLoadState("networkidle", { timeout: page.t(5000) }).catch(() => { /* a page that keeps a request open */ });
    return settledNow(5000);
  };

  const notReadOnly = (blocked) => new ControlError("not_read_only", `it tried to change the app's data (${blocked.join(", ")}), which was blocked`,
    "pass --write if that's what you meant (demo data only), or use click and type");

  const handlers = {
    async goto({ args, options: o }) {
      await page.goto(routeUrl(base, args[0]), { waitUntil: "networkidle" });
      if (o.reload) await page.reload({ waitUntil: "networkidle" });
      // A route in the same document (a hash) loads nothing, so that wait is over at once: give the page a moment to ask for
      // its data, and wait for that (a page shown from the app's cache first is refreshed by then).
      await page.waitForTimeout(150);
      await page.waitForLoadState("networkidle", { timeout: page.t(10000) }).catch(() => { /* a page that keeps a request open: the readiness check below still applies */ });
      return { route: route(), title: await page.title(), ...(await settledNow(15000)) };
    },
    async viewport({ args }) {
      await page.setViewportSize(VIEWPORTS[args[0]]);
      save({ viewport: args[0] });
      return { viewport: { name: args[0], ...VIEWPORTS[args[0]] }, route: route(), ...(await settledNow(5000)) };
    },
    info,
    async snapshot({ options: o }) {
      const root = o.root ? page.locator(o.root).first() : page.locator("body");
      if (o.root && !(await root.count())) throw new ControlError("no_match", `nothing matches ${o.root}`, `try \`${PROG} snapshot\` without --root`);
      const lines = (await root.ariaSnapshot()).split("\n");
      const max = o["max-lines"];
      return { route: route(), tree: lines.slice(0, max).join("\n"), lines: lines.length, ...(lines.length > max ? { truncated: `${lines.length - max} more lines: narrow it with --root, or raise --max-lines` } : {}) };
    },
    async screenshot({ options: o, path }) {
      const target = path ?? join(dir, "shots", `${String(++shots).padStart(3, "0")}-${shotName(route())}-${state.viewport}.png`);
      const shot = await capture(target, o.viewport ? { top: true, keepScroll: true } : {});
      return { ...shot, route: route(), viewport: state.viewport, next: "look at the image: it is the evidence" };
    },
    async "wait-settle"({ options: o }) { return { route: route(), ...(await settledNow(o.timeout)) }; },
    async click({ args, options: o }) {
      const { loc, label } = await pick(args, o);
      await loc.click({ timeout: page.t(o.timeout) });
      return { clicked: label, route: route(), ...(await afterAction()) };
    },
    async type({ args, options: o }) {
      const text = args[args.length - 1];
      const { loc, label } = await pick(args.slice(0, -1), o);
      if (await loc.evaluate((el) => el.tagName === "SELECT")) {
        const options = await loc.evaluate((el) => [...el.options].map((x) => x.label));
        const choice = options.find((x) => x === text) ?? options.find((x) => x.toLowerCase().includes(text.toLowerCase()));
        if (choice === undefined) throw new ControlError("no_option", `${label} has no option "${text}"`, `its options: ${options.join(", ")}`);
        await loc.selectOption({ label: choice }, { timeout: page.t(o.timeout) });
        return { selected: choice, in: label, route: route(), ...(await afterAction()) };
      }
      await loc.fill(text, { timeout: page.t(o.timeout) });
      return { typed: text.length, into: label, route: route(), ...(await afterAction()) };
    },
    async press({ args }) {
      await page.keyboard.press(args[0]);
      return { pressed: args[0], route: route(), ...(await afterAction()) };
    },
    async scroll({ args, options: o }) {
      const where = args.join(" ") || "down";
      const size = page.viewportSize();
      if (["up", "down", "top", "bottom"].includes(where)) {
        await page.mouse.move(size.width / 2, size.height / 2);
        await page.mouse.wheel(0, { up: -0.8, down: 0.8, top: -1000, bottom: 1000 }[where] * size.height);
      } else {
        const { loc } = await pick(args, o);
        await loc.scrollIntoViewIfNeeded();
      }
      await page.waitForTimeout(300);   // the wheel scrolls over a few frames
      return { scrolled: where, scrollY: await page.evaluate(() => globalThis.scrollY), route: route() };
    },
    async eval({ args, options: o }) {
      const code = args.join(" ");
      const blocked = [];
      const guard = (r) => {
        const m = r.request().method();
        if (["GET", "HEAD", "OPTIONS"].includes(m)) return r.fallback();
        blocked.push(`${m} ${scrubUrl(r.request().url(), base)}`);
        return r.abort("blockedbyclient");
      };
      if (!o.write) await page.route("**/*", guard);
      await page.evaluate(() => {
        globalThis.__controlChanges = 0;
        globalThis.__controlObserver?.disconnect();
        globalThis.__controlObserver = new globalThis.MutationObserver((list) => { globalThis.__controlChanges += list.length; });
        globalThis.__controlObserver.observe(globalThis.document, { subtree: true, childList: true, attributes: true, characterData: true });
      });
      let value;
      try {
        value = await page.evaluate(jsExpression(code));
      } catch (e) {
        if (blocked.length) throw notReadOnly(blocked);   // the blocked request is what made it fail
        throw new ControlError("eval_error", scrubText(firstLine(e)).replace(/^page\.evaluate: /, ""), "check the expression: it runs in the page (document, window), as an expression or a () => function");
      } finally {
        if (!o.write) await page.unroute("**/*", guard);
      }
      const domChanges = await page.evaluate(() => { globalThis.__controlObserver?.disconnect(); return globalThis.__controlChanges ?? 0; }).catch(() => 0);
      if (blocked.length) throw notReadOnly(blocked);
      const json = JSON.stringify(value);
      return { value: json === undefined ? null : json.length > 20000 ? `${json.slice(0, 20000)}… (cut)` : value, type: value === null ? "null" : Array.isArray(value) ? "array" : typeof value, domChanges, ...(domChanges && !o.write ? { warning: "the page changed while it ran (the app re-rendering, or the expression): prefer click and type to change it" } : {}) };
    },
    async console() {
      const messages = consoleBuffer.splice(0);
      return { messages, errors: messages.filter((m) => m.type === "error" || m.type === "pageerror").length };
    },
    async network() {
      const list = requests.splice(0);
      const failed = list.filter((r) => r.failed || r.status >= 400);
      return { requests: list.slice(-200), summary: { total: list.length, failed: failed.length, serverErrors: list.filter((r) => r.status >= 500).length }, ...(list.length > 200 ? { dropped: list.length - 200 } : {}) };
    },
    async flow({ args, options: o }) { return runFlow(args[0], o); },
    async reset() {
      const at = route();
      // The new server first: if it can't start, the session keeps the one it had (and says why), never none at all.
      const old = server, oldBase = base;
      try { await newServer(); } catch (e) {
        if (server && server !== old) await server.stop().catch(() => { /* it never fully started */ });
        server = old;
        base = oldBase;
        throw new ControlError("reset_failed", `couldn't start a demo server on fresh data: ${firstLine(e)}`, `the session still has its old server and data; try again, or \`${PROG} stop\` and \`${PROG} start\``);
      }
      await old.stop().catch((e) => log(`stopping the old demo server: ${firstLine(e)}`));
      save({ base, port: server.port });
      await page.goto(`${base}${at}`, { waitUntil: "networkidle" });
      return { reset: true, server: base, route: route(), seed: opt.seed, ...(await settledNow(15000)) };
    },
  };

  async function runFlow(name, o) {
    const { flows, error } = readFlows(join(HERE, "flows"));
    if (error) throw new ControlError("bad_flow", error, "fix the flow file (frontend/verify/flows/README.md has the format)");
    const flow = flows.find((f) => f.name === name);
    if (!flow) {
      const near = closest(name, flows.map((f) => f.name));
      throw new ControlError("no_such_flow", `no flow named ${name}`, near ? `did you mean ${near}? (\`${PROG} flows\` lists them)` : `\`${PROG} flows\` lists them`);
    }
    if (flow.signed_in && !opt.signedIn) throw new ControlError("needs_signed_in", `${name} runs signed in, and this session isn't`, `\`${PROG} stop\`, then \`${PROG} start --signed-in\``);
    if (o.viewport && o.viewport !== state.viewport) await handlers.viewport({ args: [o.viewport] });
    const vp = state.viewport;
    const notes = [];
    if (flow.viewports && !flow.viewports.includes(vp)) notes.push(`make verify runs ${name} at ${flow.viewports.join(", ")} only; this is ${vp}`);
    if (!flow.signed_in && opt.signedIn) notes.push("make verify runs this flow on a server without sign-in");
    notes.push("it ran on this session's data: `reset` first for the data make verify starts it from");
    consoleBuffer.length = 0;
    requests.length = 0;
    const files = [], steps = [], problems = [];
    const out = join(dir, "flows");
    // Named as make verify names them (flow-<name>-<step>-<width>.png, and flow-<name>-<width>.png at the end), in artifacts/control/flows/.
    const shotAs = async (file) => { files.push((await capture(join(out, screenshotFile(file, vp)), { keepScroll: !!page.scrolledByFlow })).path); };
    page.checks = [];
    page.scrolledByFlow = false;
    await page.goto(`${base}/#${flow.page ?? "overview"}`, { waitUntil: "networkidle" });
    await waitSettled(page, page.t(15000));
    for (const [i, step] of flow.steps.entries()) {
      const started = Date.now();
      try {
        await runStep(page, step, (n) => shotAs(`flow-${flow.name}-${n}`));
        steps.push({ n: i + 1, step: JSON.stringify(step), ok: true, ms: Date.now() - started });
      } catch (e) {
        steps.push({ n: i + 1, step: JSON.stringify(step), ok: false, ms: Date.now() - started, error: scrubText(firstLine(bad(e).message)) });
        const failedShot = join(out, screenshotFile(`flow-${flow.name}-FAILED`, vp));
        await page.screenshot({ path: failedShot, fullPage: true }).then(() => files.push(rel(failedShot))).catch(() => { /* best effort: the step's error says why */ });
        problems.push(`step ${i + 1} failed`);
        break;
      }
    }
    if (!problems.length) {
      try { await shotAs(`flow-${flow.name}`); } catch (e) { problems.push(`the last screenshot: ${firstLine(e)}`); }
    }
    for (const m of consoleBuffer) if (m.type === "error" || m.type === "pageerror") problems.push(`console ${m.type}: ${m.text}`);
    for (const r of requests) if (r.status >= 500) problems.push(`${r.status} from ${r.method} ${r.path}`);
    const result = { flow: name, viewport: vp, steps, files, notes };
    if (problems.length) throw Object.assign(new ControlError("flow_failed", `${name} failed: ${problems.join("; ")}`, "the -FAILED- image and `console` show what the page did; make verify would fail the same way"), { data: result });
    return result;
  }

  let queue = Promise.resolve();
  const sameToken = (t) => typeof t === "string" && t.length === token.length && timingSafeEqual(Buffer.from(t), Buffer.from(token));

  async function handle(request) {
    if (!sameToken(request.token)) return errorResult(request.command, new ControlError("forbidden", "wrong token: this isn't the session's caller", `use ${PROG}, which reads the token from the state file`));
    if (request.command === "stop") { setTimeout(() => shutdown("stop"), 50); return { ok: true, stopping: true }; }
    const handler = handlers[request.command];
    if (!handler) { touch(); return errorResult(request.command, new ControlError("usage", `the session has no command ${request.command}`, `\`${PROG} --help\``, 2)); }
    log(`${request.command}`);
    // Not idle while a command runs (a long flow can outlast a short --idle-minutes): the clock starts again when it ends.
    clearTimeout(idle);
    try {
      const reply = await handler({ args: request.args ?? [], options: request.options ?? {}, path: request.path });
      const away = offsite.splice(0);
      return { ok: true, ...reply, ...(away.length ? { warning: `the page went to ${away.join(", ")}, which isn't the session's server: it was brought back` } : {}) };
    } catch (e) {
      const err = bad(e);
      return { ...errorResult(request.command, err), ...(e.data ?? {}) };
    } finally {
      touch();
    }
  }

  if (existsSync(socket)) rmSync(socket, { force: true });
  listener = createServer((conn) => {
    let data = "";
    conn.on("data", (d) => {
      data += d;
      if (data.length > 1e6) { conn.destroy(); return; }
      const end = data.indexOf("\n");
      if (end < 0) return;
      let request;
      try { request = JSON.parse(data.slice(0, end)); } catch { conn.end(JSON.stringify(errorResult(null, new ControlError("usage", "not a request", `use ${PROG}`)))); return; }
      queue = queue.then(() => handle(request)).then((reply) => { conn.end(JSON.stringify(reply)); });
    });
    conn.on("error", () => { /* the caller went away: its reply has nowhere to go */ });
  });
  await new Promise((resolve, reject) => { listener.once("error", reject); listener.listen(socket, resolve); });
  chmodSync(socket, 0o600);
  // Screenshots are numbered on from what's already there, so a new session doesn't overwrite the last one's.
  try { shots = lastShotNumber(readdirSync(join(dir, "shots"))); } catch { shots = 0; /* no screenshots yet */ }
  touch();
  save({ status: "ready", base, port: server.port, route: route(), clock: { frozenDay: opt.day, browser: await page.evaluate(() => new Date().toString()) } });
  log(`ready at ${base}`);
}

main().catch((e) => { log(`fatal: ${e.stack ?? e}`); process.exit(1); });
