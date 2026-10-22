// tools/control end to end, on the real demo server under the frozen clock (`make control-smoke`; the Verify flows
// workflow runs it): doctor, start, info, snapshot, screenshot, a flow, reset, stop, then checks nothing is left
// running. It uses a control folder of its own (artifacts/control-smoke/), so it never touches a session you have open.
// Prints each command and its answer; exits 1 on the first that isn't what it should be.
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "../..");
const dir = join(root, "artifacts", "control-smoke");
const env = { ...process.env, RUNWAY_CONTROL_DIR: dir };

function control(...args) {
  console.log(`$ tools/control ${args.join(" ")}`);
  const r = spawnSync(process.execPath, [join(here, "control.mjs"), ...args], { cwd: root, env, encoding: "utf8", timeout: 300000 });
  const out = (r.stdout ?? "").trim();
  console.log(out.length > 1500 ? `${out.slice(0, 1500)}…` : out);
  let json = null;
  try { json = JSON.parse(out); } catch { /* --text or help: checked by the caller */ }
  return { code: r.status, json, out };
}

function expect(ok, what) {
  if (ok) { console.log(`  ok: ${what}`); return; }
  console.error(`  FAIL: ${what}`);
  control("stop");
  process.exit(1);
}

const pngWidth = (file) => readFileSync(file).readUInt32BE(16);

rmSync(dir, { recursive: true, force: true });
let r = control("doctor");
expect(r.json && r.json.checks.filter((c) => !c.ok && c.name !== "machine load").length === 0, "doctor: every check passes (the machine's load aside)");

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

r = control("flow", "transactions-delete", "--viewport", "desktop");
expect(r.code === 0 && r.json?.steps?.every((s) => s.ok) && r.json.files.length === 4, "flow: transactions-delete passes, step by step, with its 4 images");

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
