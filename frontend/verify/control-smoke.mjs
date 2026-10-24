// tools/control end to end, on the real demo server under the frozen clock (`make control-smoke`; the Verify flows
// workflow runs it): doctor, start, info, snapshot, screenshot, what the session must never do (reach another server,
// change the data from a read-only eval), a flow, reset, stop, then checks nothing is left running. It uses a control folder of its own (artifacts/control-smoke/), so it never touches a session you have open.
// Prints each command and its answer; exits 1 on the first that isn't what it should be.
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "../..");
const dir = join(root, "artifacts", "control-smoke");
// Credentials in the caller's environment, which the session must never pass on to its processes (checked below).
const FAKE_SECRET = "control-smoke-not-a-real-key";
const env = { ...process.env, RUNWAY_CONTROL_DIR: dir, OPENROUTER_API_KEY: FAKE_SECRET, GITHUB_TOKEN: FAKE_SECRET, SOME_SERVICE_TOKEN: FAKE_SECRET };

function control(...args) { return controlWith(env, ...args); }

function controlWith(withEnv, ...args) {
  console.log(`$ tools/control ${args.join(" ")}`);
  const r = spawnSync(process.execPath, [join(here, "control.mjs"), ...args], { cwd: root, env: withEnv, encoding: "utf8", timeout: 300000 });
  const out = (r.stdout ?? "").trim();
  console.log(out.length > 1500 ? `${out.slice(0, 1500)}…` : out);
  let json = null;
  try { json = JSON.parse(out); } catch { /* --text or help: checked by the caller */ }
  return { code: r.status, json, out };
}

function expect(ok, what, explain = () => {}) {
  if (ok) { console.log(`  ok: ${what}`); return; }
  console.error(`  FAIL: ${what}`);
  explain();
  control("stop");
  process.exit(1);
}

/** For a flow that should have passed: the step that failed and why, in full, and the page's last 20 console lines. */
function explainFlow(r) {
  return () => {
    console.error(`  the flow's answer: ${r.json?.error ? `${r.json.error.code}: ${r.json.error.message}` : "(no error)"}`);
    for (const s of r.json?.steps ?? []) if (!s.ok) console.error(`  step ${s.n} failed: ${s.step}\n    ${s.error}`);
    const c = spawnSync(process.execPath, [join(here, "control.mjs"), "console"], { cwd: root, env, encoding: "utf8", timeout: 60000 });
    let messages = [];
    try { messages = JSON.parse(c.stdout).messages ?? []; } catch { console.error(`  (no console: ${c.stdout.trim().slice(0, 300)})`); }
    console.error(`  the page's last ${Math.min(20, messages.length)} console lines:`);
    for (const m of messages.slice(-20)) console.error(`    [${m.type}] ${m.route ?? ""} ${m.text}`);
  };
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** A server on another loopback port that counts what reaches it: the session must never send it anything. */
async function countingServer() {
  const seen = [];
  const server = createServer((req, res) => { seen.push(`${req.method} ${req.url}`); res.end("<a href='/'>counted</a>"); });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  return { seen, url: `http://127.0.0.1:${server.address().port}`, close: () => new Promise((r) => { server.close(r); server.closeAllConnections(); }) };
}

const pngWidth = (file) => readFileSync(file).readUInt32BE(16);

rmSync(dir, { recursive: true, force: true });
let r = control("doctor");
expect(r.json && r.json.checks.filter((c) => !c.ok && c.name !== "machine load").length === 0, "doctor: every check passes (the machine's load aside)");

// A start that fails says why: here a "Chromium" that exits at once (the session's own browser lookup finds it first).
const fake = join(dir, "fake-browsers");
mkdirSync(join(fake, "chromium-0", "chrome-linux"), { recursive: true });
writeFileSync(join(fake, "chromium-0", "chrome-linux", "chrome"), "#!/bin/sh\necho 'not a browser' >&2\nexit 1\n", { mode: 0o755 });
r = controlWith({ ...env, PLAYWRIGHT_BROWSERS_PATH: fake }, "start", "--anyway", "--plain");
expect(r.code === 1 && r.json?.error?.code === "start_failed" && !r.json.error.message.includes("its process ended") && !existsSync(join(dir, "state.json")),
  "a start that fails reports the session's own error, and leaves no state behind");
rmSync(fake, { recursive: true, force: true });

// --anyway: a busy CI runner or container shouldn't fail the smoke test (start refusing on a busy machine is unit-tested).
r = control("start", "--anyway");
expect(r.code === 0 && r.json?.ok && r.json.url.startsWith("http://127.0.0.1:"), "start: a session on a loopback demo server");
expect(r.json.clock.browser.startsWith("Wed Jun 17 2026 12:00:00"), "the browser's clock is at the frozen day, noon");

r = control("start");
expect(r.code === 1 && r.json?.error?.code === "already_running", "a second start is refused, and says what to do");

r = control("info");
expect(r.json?.ok && r.json.viewport.width === 440 && r.json.route === "/#overview", "info: the phone width, on Overview");

r = control("snapshot", "--text");
expect(r.code === 0 && r.out.includes('heading "Overview" [level=1]'), "snapshot: the accessibility tree, with the page's heading");

r = control("screenshot");
const shot = r.json?.path && join(root, r.json.path);
expect(r.json?.ok && existsSync(shot) && pngWidth(shot) === 440, "screenshot: a full-page PNG at the phone's width, under artifacts/");
const baseline = join(root, "screenshots", "overview-phone.png");
// The same as make verify's image when the browser is the same build as the one that made it (not on every runner).
console.log(`  note: ${readFileSync(shot).equals(readFileSync(baseline)) ? "byte for byte the same as" : "differs from (another Chromium build?)"} screenshots/overview-phone.png`);

r = control("click", "button", "Definitely not on the page", "--timeout", "500");
expect(r.code === 1 && r.json?.error?.code === "no_match" && r.json.error.hint.includes("snapshot"), "a click on nothing fails with what to do instead");

r = control("goto", "https://example.com");
expect(r.code === 1 && r.json?.error?.code === "refused", "goto anywhere but its own server is refused");

r = control("eval", "() => fetch('/api/control-smoke', { method: 'POST' }).then((r) => r.status)");
expect(r.code === 1 && r.json?.error?.code === "not_read_only", "eval is read-only without --write: a POST is blocked, and said so");
r = control("eval", "() => { setTimeout(() => fetch('/api/control-smoke', { method: 'POST', headers: { 'X-Runway': '1' } }), 100); return 'later'; }");
expect(r.code === 1 && r.json?.error?.code === "not_read_only", "a write an eval sets off on a short timer fails it too (not_read_only)");
// The session's server must never get any of these: a request to /api/control-smoke with an answer means one got through.
const reachedServer = () => (control("network").json?.requests ?? []).filter((q) => q.path === "/api/control-smoke" && q.status);
r = control("eval", "() => { setTimeout(() => fetch('/api/control-smoke', { method: 'DELETE', headers: { 'X-Runway': '1' } }), 2500); return 'much later'; }");
expect(r.code === 0 && r.json?.value === "much later" && r.json.timersLeft === 1 && /left 1 timer running/.test(r.json.warning ?? ""),
  "an eval whose write comes later answers first, and says it left a timer running");
await sleep(3500);
r = control("info");
expect(r.code === 0 && /change to the app's data was blocked before it was sent .*: DELETE \/api\/control-smoke/.test(r.json?.warning ?? ""),
  "the later write was blocked all the same, and the next command says so");
expect(!reachedServer().length, "the server never got it");

// The guard is off while a command acts, so what a read-only eval leaves behind must not get through then either.
r = control("eval", "() => { setTimeout(() => fetch('/api/control-smoke', { method: 'POST', headers: { 'X-Runway': '1' } }), 1500); return 'during the next command'; }");
expect(r.code === 0 && r.json?.timersLeft === 1, "a read-only eval with a 1.5 s write timer answers, saying it left a timer running");
r = control("goto", "overview");
expect(r.code === 0 && /stopped 1 timer a read-only eval left running/.test(r.json?.warning ?? ""), "the goto right after it stops that timer first, and says so");
await sleep(2500);
control("info");
expect(!reachedServer().length, "the server never got the POST");
r = control("eval", "() => { document.addEventListener('click', () => fetch('/api/control-smoke', { method: 'POST', headers: { 'X-Runway': '1' } }), { once: true }); return 'on the next click'; }");
expect(r.code === 0, "a read-only eval leaves a listener that writes on the next click");
r = control("click", "heading", "Overview");
await sleep(1000);
const after = control("info");
expect(r.code === 0 && /blocked before it was sent .*POST \/api\/control-smoke/.test(`${r.json?.warning ?? ""} ${after.json?.warning ?? ""}`) && !reachedServer().length,
  "a click (the write guard off) sets it off, and its write is refused in the page all the same, and said so");
// The network's guard, the second layer: an eval --write may write while it runs, but not on a timer once it's done.
r = control("eval", "--write", "() => { setTimeout(() => fetch('/api/control-smoke', { method: 'DELETE', headers: { 'X-Runway': '1' } }), 2500); return 'later'; }");
expect(r.code === 0 && !r.json.timersLeft, "eval --write can leave a timer (its writes aren't refused in the page)");
await sleep(3500);
r = control("info");
expect(/blocked before it was sent .*DELETE \/api\/control-smoke/.test(r.json?.warning ?? "") && !reachedServer().length,
  "its write, sent while no command acts, is blocked by the network guard, and the next command says so");
control("console");

// Nothing reaches another server, not even one on this machine (your own Runway, say): a link to it, a fetch, an image.
const counting = await countingServer();
r = control("eval", "--write", `() => { const a = document.createElement('a'); a.href = '${counting.url}/#elsewhere'; a.textContent = 'Smoke elsewhere'; a.style = 'position:fixed;top:40%;left:20px;z-index:99999;padding:20px;background:#fff'; document.body.append(a); return true; }`);
expect(r.code === 0, "a link to another loopback server is put on the page");
r = control("click", "link", "Smoke elsewhere");
await sleep(500);
expect(r.code === 0 && counting.seen.length === 0 && /127\.0\.0\.1:\d+\/, which isn't the session's server, were blocked before they were sent/.test(r.json?.warning ?? ""),
  `a click on it sends nothing (the other server saw ${counting.seen.length} request(s)), and the answer says it was blocked before it was sent`);
r = control("info");
expect(r.code === 0 && r.json.route.startsWith("/") && r.json.session.server !== counting.url, "the page stays on the session's server");
r = control("eval", `() => { new Image().src = '${counting.url}/pixel.png'; return fetch('${counting.url}/data', { mode: 'no-cors' }).then(() => 'sent', () => 'blocked'); }`);
await sleep(500);
// (The app's own Content-Security-Policy stops these in the page, before the session's guard sees them.)
expect(r.code === 0 && r.json.value === "blocked" && counting.seen.length === 0, `a fetch or an image from it is never sent (the other server saw ${counting.seen.length})`);
r = control("eval", "() => fetch('https://example.com/').then(() => 'reached', () => 'blocked')");
expect(r.code === 0 && r.json.value === "blocked", "and nothing on the internet is reached");
await fetch(`${counting.url}/control`).then((x) => x.text());
expect(counting.seen.length === 1, "(the counting server does count what reaches it)");
await counting.close();

// The session's processes never see the caller's credentials (runway/verify.py's KEPT_ENV).
const session = JSON.parse(readFileSync(join(dir, "state.json"), "utf8"));
const leaked = [session.pid, ...readFileSync(`/proc/${session.pid}/task/${session.pid}/children`, "utf8").trim().split(/\s+/).filter(Boolean).map(Number)]
  .filter((pid) => { try { return readFileSync(`/proc/${pid}/environ`, "utf8").includes(FAKE_SECRET); } catch { return false; } });
expect(!leaked.length, `no credential from the caller's environment in the session's processes (leaked into ${leaked.join(", ") || "none"})`);

// What an acting command sets off still saves: the Retirement planner saves itself 700 ms after typing stops, after
// `type` has answered; the guard comes back on only after that (a command sent sooner waits for it).
control("goto", "networth/retirement");
r = control("click", "button", "Show my projection");
expect(r.code === 0, "the Retirement planner, its first plan saved");
control("network");
r = control("type", "spinbutton", "Plan until age", "96");
const typed = r;
r = control("snapshot");
const saved = control("network");
expect(typed.code === 0 && !typed.json.warning && r.code === 0 && !r.json.warning
  && saved.json?.requests?.some((q) => q.method === "POST" && q.path === "/api/investments/plan" && q.status === 200), "a field that saves itself after typing still saves");

r = control("reset");
expect(r.json?.ok && r.json.reset, "reset before the flow: the data make verify starts it from");
r = control("flow", "transactions-delete", "--viewport", "desktop");
expect(r.code === 0 && r.json?.steps?.every((s) => s.ok) && r.json.files.length === 4, "flow: transactions-delete passes, step by step, with its 4 images", explainFlow(r));

// A flow that fails keeps its step report: at the phone's width the rows' Delete buttons only show on hover (gotchas.md).
r = control("flow", "transactions-delete", "--viewport", "phone");
expect(r.code === 1 && r.json?.error?.code === "flow_failed" && r.json.steps?.some((s) => !s.ok && s.error)
  && r.json.files?.some((f) => f.includes("-FAILED-")), "a failed flow says which step failed, why, and where its -FAILED- image is");

r = control("reset", "--dry-run");
expect(r.json?.dryRun === true, "reset --dry-run says what it would do");
r = control("reset");
expect(r.json?.ok && r.json.reset, "reset: fresh data on a new server");

const pid = JSON.parse(readFileSync(join(dir, "state.json"), "utf8")).pid;
r = control("stop");
expect(r.json?.ok && r.json.stopped, "stop");
let alive = true;
try { process.kill(pid, 0); } catch { alive = false; }
expect(!alive && !existsSync(join(dir, "state.json")) && !existsSync(join(dir, `session-${pid}`)), "nothing left: the daemon gone, its state and data removed");
r = control("doctor");
expect(r.json?.checks.find((c) => c.name === "leftover processes")?.ok, "doctor finds no leftover processes");
console.log("control-smoke: passed");
