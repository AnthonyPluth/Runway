// tools/control (control.mjs): parsing, help, the state file, the JSON error shape, --dry-run and stale sessions. The
// real session (a demo server and a browser) is control-smoke.mjs's, run by `make control-smoke` and the Verify flows workflow.
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

const ARTIFACTS = join(import.meta.dirname, "../../artifacts");
mkdirSync(ARTIFACTS, { recursive: true });
// The commands under test use a folder of their own under artifacts/ (RUNWAY_CONTROL_DIR), never artifacts/control/.
const DIR = mkdtempSync(join(ARTIFACTS, "control-test-"));
process.env.RUNWAY_CONTROL_DIR = DIR;
const c = await import("./control.mjs");

afterAll(() => rmSync(DIR, { recursive: true, force: true }));

describe("parseArgs", () => {
  it("takes the command, its arguments and options, with defaults", () => {
    expect(c.parseArgs(["click", "button", "Save", "--nth", "2"])).toEqual({ command: "click", args: ["button", "Save"], options: { nth: 2, timeout: 10000 }, text: false, help: false });
    expect(c.parseArgs(["start", "--viewport=desktop", "--investments", "--text"]).options).toMatchObject({ viewport: "desktop", investments: true, "idle-minutes": 30 });
    expect(c.parseArgs(["start", "--text"]).text).toBe(true);
  });

  it("passes everything after -- as arguments", () => {
    expect(c.parseArgs(["type", "textbox", "Amount", "--", "-5"]).args).toEqual(["textbox", "Amount", "-5"]);
  });

  it("asks for help without checking the arguments", () => {
    expect(c.parseArgs(["click", "--help"])).toMatchObject({ command: "click", help: true });
    expect(c.parseArgs([])).toMatchObject({ command: null });
  });

  const usage = (argv) => { try { c.parseArgs(argv); } catch (e) { return { code: e.code, exit: e.exit, message: e.message, hint: e.hint }; } return null; };

  it("says what was meant for a misspelt command or option", () => {
    expect(usage(["clik"])).toMatchObject({ code: "usage", exit: 2, hint: expect.stringContaining("tools/control click") });
    expect(usage(["click", "x", "--nht", "1"])).toMatchObject({ message: "click has no option --nth".replace("nth", "nht"), hint: expect.stringContaining("--nth") });
  });

  it("checks values, counts and choices", () => {
    expect(usage(["click"])).toMatchObject({ code: "usage", message: "click needs <target…>" });
    expect(usage(["goto", "a", "b"])?.message).toMatch(/goto takes <route>/);
    expect(usage(["viewport", "tablet"])?.message).toBe("viewport takes phone or desktop, not \"tablet\"");
    expect(usage(["start", "--viewport", "tablet"])?.message).toMatch(/--viewport is phone or desktop/);
    expect(usage(["click", "x", "--nth", "two"])?.message).toMatch(/--nth is a number/);
    expect(usage(["click", "x", "--nth"])?.message).toBe("--nth needs a value");
    expect(usage(["stop", "--dry-run=yes"])?.message).toBe("--dry-run takes no value");
    expect(usage(["screenshot", "--full", "--viewport"])?.message).toMatch(/not both/);
    expect(usage(["start", "--plain", "--receipt"])?.message).toMatch(/--plain/);
    expect(usage(["--nth", "1"])?.message).toMatch(/goes after a command/);
  });

  it("seeds what make verify does unless told otherwise", () => {
    expect(c.seedOptions({})).toEqual(["ai-buttons", "investments", "receipt", "subcategories"]);
    expect(c.seedOptions({ receipt: true })).toEqual(["receipt"]);
    expect(c.seedOptions({ plain: true })).toEqual([]);
  });
});

describe("targets, routes and what's reported", () => {
  it("reads a role and name, a selector or text", () => {
    expect(c.parseTarget(["button", "Save", "draft"])).toEqual({ kind: "role", role: "button", name: "Save draft" });
    expect(c.parseTarget(["dialog"])).toEqual({ kind: "role", role: "dialog" });
    for (const s of ["#amount", ".row", "[data-tx]", "css=div", "text=Save", "button[aria-label^='Delete ']", "button:has-text('Save')", "nav > a"]) {
      expect(c.parseTarget([s])).toEqual({ kind: "selector", selector: s });
    }
    expect(c.parseTarget(["Add", "transaction"])).toEqual({ kind: "text", text: "Add transaction" });
    expect(c.parseTarget(["Button"])).toEqual({ kind: "text", text: "Button" });
    expect(() => c.parseTarget([""])).toThrow(/no target/);
  });

  it("opens routes on the session's server only", () => {
    const base = "http://127.0.0.1:4000";
    expect(c.routeUrl(base, "budget")).toBe(`${base}/#budget`);
    expect(c.routeUrl(base, "#setup/categories")).toBe(`${base}/#setup/categories`);
    expect(c.routeUrl(base, "/#transactions")).toBe(`${base}/#transactions`);
    expect(c.routeUrl(base, `${base}/#budget`)).toBe(`${base}/#budget`);
    for (const other of ["https://example.com", "http://127.0.0.1:8080/#budget", "http://localhost:4000/", "file:///etc/passwd"]) {
      expect(() => c.routeUrl(base, other)).toThrow(/isn't this session's demo server/);
    }
  });

  it("reports paths without query strings or ids, and console text without amounts or tokens", () => {
    const base = "http://127.0.0.1:4000";
    expect(c.scrubUrl(`${base}/api/transactions/123?q=coffee&account=7`, base)).toBe("/api/transactions/:id");
    expect(c.scrubUrl("https://cdn.example.com/x.js?v=1#a", base)).toBe("cdn.example.com/x.js");
    expect(c.scrubUrl("data:image/png;base64,AAAA", base)).toBeNull();
    expect(c.scrubText("GET http://x/api/a?account=9 failed: $1,234.56 for a@b.co with abcdefghijklmnopqrstuvwxyz0123456789"))
      .toBe("GET http://x/api/a?… failed: <amount> for <email> with <token>");
  });

  it("calls a function and evaluates an expression", () => {
    expect(c.jsExpression("document.title")).toBe("document.title");
    expect(c.jsExpression("() => 1")).toBe("(() => 1)()");
    expect(c.jsExpression("async () => 1")).toBe("(async () => 1)()");
    expect(c.jsExpression("function () { return 1 }")).toBe("(function () { return 1 })()");
  });

  it("writes screenshots under artifacts/ only, as PNG", () => {
    expect(c.screenshotTarget(undefined)).toBeNull();
    expect(c.screenshotTarget("artifacts/control/a.png")).toBe(join(c.ROOT, "artifacts/control/a.png"));
    for (const p of ["screenshots/overview-phone.png", "/tmp/a.png", "artifacts/../screenshots/a.png", "artifacts"]) expect(() => c.screenshotTarget(p)).toThrow(/isn't under artifacts/);
    expect(() => c.screenshotTarget("artifacts/a.jpg")).toThrow(/\.png/);
    expect(c.shotName("/#setup/categories")).toBe("setup-categories");
    expect(c.shotName("/")).toBe("overview");
  });

  it("uses a control folder under artifacts/ only", () => {
    expect(c.controlDir(undefined)).toBe(join(c.ARTIFACTS, "control"));
    expect(c.controlDir("/tmp/x")).toBe(join(c.ARTIFACTS, "control"));
    expect(c.controlDir(join(c.ARTIFACTS, "t"))).toBe(join(c.ARTIFACTS, "t"));
    expect(c.CONTROL_DIR).toBe(DIR);
  });
});

describe("the JSON error shape", () => {
  it("is ok: false with a code, a message and what to do, never a stack", () => {
    const e = c.errorResult("click", new c.ControlError("no_match", "nothing matches", "run snapshot"));
    expect(e).toEqual({ ok: false, command: "click", error: { code: "no_match", message: "nothing matches", hint: "run snapshot" } });
    const internal = c.errorResult("goto", new Error("boom\n    at x (y.js:1:1)"));
    expect(internal.error).toMatchObject({ code: "internal", message: "boom" });
    expect(JSON.stringify(internal)).not.toMatch(/at x/);
  });

  it("comes out of run() with exit 2 for usage and 1 for a failure, and renders as text", async () => {
    const bad = await c.run(["viewport", "tablet"]);
    expect(bad).toMatchObject({ exit: 2, result: { ok: false, command: "viewport", error: { code: "usage" } } });
    expect((await c.run(["clik"])).result).toMatchObject({ ok: false, command: null, error: { code: "usage" } });
    const none = await c.run(["info", "--text"]);
    expect(none).toMatchObject({ exit: 1, text: true, result: { ok: false, command: "info", error: { code: "no_session" } } });
    expect(c.renderText(none.result)).toBe("error (no_session): no session is running\n  what to do: run `tools/control start` first (`tools/control doctor` if it fails)");
  });
});

describe("help", () => {
  it("lists every command in the overview and has help for each", async () => {
    const overview = (await c.run(["--help"])).help;
    for (const name of Object.keys(c.COMMANDS)) {
      expect(overview).toContain(`  ${name} `);
      expect((await c.run([name, "--help"])).help).toBe(c.commandHelp(name));
    }
    expect((await c.run(["help", "click"])).help).toBe(c.commandHelp("click"));
  });

  it("makes references/cli.md from the same help, the same every time", async () => {
    const md = c.markdownReference();
    expect(md).toBe(c.markdownReference());
    expect(md).toContain(c.commandHelp("screenshot"));
    expect((await c.run(["help", "--markdown"])).help).toBe(md.trimEnd());
  });

  it("knows which commands go to the session", () => {
    for (const name of c.SESSION_COMMANDS) expect(c.COMMANDS[name]).toBeDefined();
    expect(c.SESSION_COMMANDS).not.toContain("start");
  });
});

describe("the state file and stale sessions", () => {
  const dir = join(DIR, "state");
  beforeAll(() => mkdirSync(dir, { recursive: true }));

  it("round-trips, readable by this user only", () => {
    c.writeState({ pid: 4242, status: "ready", token: "t" }, dir);
    expect(c.readState(dir)).toEqual({ version: c.STATE_VERSION, pid: 4242, status: "ready", token: "t" });
    expect(statSync(c.statePath(dir)).mode & 0o777).toBe(0o600);
    c.removeState(dir);
    expect(c.readState(dir)).toBeNull();
  });

  it("calls a half-written or foreign file corrupt", () => {
    writeFileSync(c.statePath(dir), "{\"pid\": 1");
    expect(c.readState(dir)).toEqual({ corrupt: true });
    writeFileSync(c.statePath(dir), JSON.stringify({ version: 99, pid: 1 }));
    expect(c.readState(dir)).toEqual({ corrupt: true });
    c.removeState(dir);
  });

  it("is stale when its daemon is gone or its pid is another process now", () => {
    const state = { version: 1, pid: 4242, status: "ready" };
    expect(c.sessionStatus(null)).toBe("none");
    expect(c.sessionStatus({ corrupt: true })).toBe("corrupt");
    expect(c.sessionStatus(state, { isDaemon: () => true })).toBe("ready");
    expect(c.sessionStatus({ ...state, status: "starting" }, { isDaemon: () => true })).toBe("starting");
    expect(c.sessionStatus(state, { isDaemon: () => false })).toBe("stale");
    // A live process that isn't a control daemon for this folder (the pid was reused): stale.
    expect(c.isOurDaemon(process.pid, dir)).toBe(false);
  });

  it("tells a command what to do for each state", () => {
    const code = (state, isDaemon = () => true) => {
      if (state) c.writeState(state, dir); else c.removeState(dir);
      try { c.requireSession(dir, { isDaemon }); return "usable"; } catch (e) { return `${e.code}: ${e.hint}`; }
    };
    expect(code(null)).toMatch(/^no_session: run `tools\/control start`/);
    expect(code({ pid: 1, status: "ready" }, () => false)).toMatch(/^stale_session: run `tools\/control cleanup`/);
    expect(code({ pid: 1, status: "starting" })).toMatch(/^starting/);
    expect(code({ pid: 1, status: "failed", error: "x" })).toMatch(/^start_failed/);
    expect(code({ pid: 1, status: "ready" })).toBe("usable");
    c.removeState(dir);
  });

  it("finds this folder's daemon by its command line, nothing else", () => {
    const proc = join(DIR, "proc");
    const fake = (pid, cmd, env = "") => { mkdirSync(join(proc, String(pid)), { recursive: true }); writeFileSync(join(proc, String(pid), "cmdline"), cmd.split(" ").join("\0")); writeFileSync(join(proc, String(pid), "environ"), env); writeFileSync(join(proc, String(pid), "stat"), `${pid} (x) S 77 0`); };
    fake(11, `node /r/frontend/verify/control-daemon.mjs --dir ${dir} --python p`);
    fake(12, "python run.py --host 127.0.0.1 --port 5", `PATH=/bin\0RUNWAY_DATA=${dir}/session-11/data-1-1\0`);
    fake(13, `python -m runway.verify serve --parent 11 --seed s --data ${dir}/session-11/data-2-1 --port 6`);
    fake(14, "python run.py --host 127.0.0.1 --port 7", "RUNWAY_DATA=/tmp/runway-verify-x/servers/server-1-1\0");   // make verify's: never
    fake(15, `node /r/frontend/verify/control-daemon.mjs --dir ${dir}-other`);                                             // another checkout's
    expect(c.isOurDaemon(11, dir, proc)).toBe(true);
    expect(c.isOurDaemon(15, dir, proc)).toBe(false);
    expect(c.controlProcesses(dir, proc).map((p) => [p.pid, p.what])).toEqual([[11, "control daemon"], [12, "demo server"], [13, "demo server"]]);
  });
});

describe("cleanup and --dry-run", () => {
  const dir = join(DIR, "plan");
  beforeAll(() => {
    for (const d of ["session-11", "session-12", "shots", "flows"]) mkdirSync(join(dir, d), { recursive: true });
    writeFileSync(join(dir, "start.lock"), "999999999");
  });

  it("removes everything a crashed session left, and keeps the screenshots unless asked", () => {
    const entries = ["session-11", "session-12", "shots", "flows", "state.json", "control.sock", "start.lock", "daemon.log"];
    const processes = [{ pid: 21, parent: 1, what: "demo server" }];
    const plan = c.cleanupPlan({ dir, state: { pid: 11, socket: "/tmp/runway-control-x.sock" }, processes, entries, alive: () => false });
    expect(plan.remove.map((p) => p.replace(`${dir}/`, ""))).toEqual(["session-11", "session-12", "state.json", "control.sock", "start.lock", "/tmp/runway-control-x.sock"]);
    expect(plan.kill).toEqual(processes);
    expect(c.cleanupPlan({ dir, processes, entries, shots: true, alive: () => false }).remove.map((p) => p.replace(`${dir}/`, ""))).toContain("shots");
  });

  it("leaves a running session and its processes alone", () => {
    const entries = ["session-11", "session-12", "state.json", "control.sock", "start.lock"];
    const processes = [{ pid: 11, parent: 1, what: "control daemon" }, { pid: 31, parent: 11, what: "demo server" }, { pid: 41, parent: 1, what: "demo server" }];
    const plan = c.cleanupPlan({ dir, state: { pid: 11, sessionDir: join(dir, "session-11") }, live: 11, processes, entries, alive: (pid) => pid === 5 });
    expect(plan.remove).toEqual([join(dir, "session-12"), join(dir, "start.lock")]);
    expect(plan.kill.map((p) => p.pid)).toEqual([41]);
  });

  it("dry-run says what cleanup and stop would do, and does none of it", async () => {
    c.writeState({ pid: 999999999, status: "ready", socket: join(DIR, "control.sock") }, DIR);
    mkdirSync(join(DIR, "session-999999999"), { recursive: true });
    const cleanup = await c.run(["cleanup", "--dry-run"]);
    expect(cleanup.result).toMatchObject({ ok: true, dryRun: true });
    expect(cleanup.result.wouldRemove).toEqual(expect.arrayContaining([expect.stringMatching(/session-999999999$/), expect.stringMatching(/state\.json$/)]));
    const stop = await c.run(["stop", "--dry-run"]);
    expect(stop.result).toMatchObject({ ok: true, dryRun: true, stale: true });
    expect(existsSync(c.statePath(DIR))).toBe(true);
    expect(existsSync(join(DIR, "session-999999999"))).toBe(true);
    // Without --dry-run the stale session goes.
    const done = await c.run(["cleanup"]);
    expect(done.result.removed).toEqual(expect.arrayContaining([expect.stringMatching(/state\.json$/)]));
    expect(existsSync(c.statePath(DIR))).toBe(false);
    expect(existsSync(join(DIR, "session-999999999"))).toBe(false);
  });

  it("stopping with no session is not an error", async () => {
    expect((await c.run(["stop"])).result).toEqual({ ok: true, command: "stop", stopped: false, message: "no session was running" });
  });

  it("reset --dry-run needs a session, and says so", async () => {
    expect((await c.run(["reset", "--dry-run"])).result.error.code).toBe("no_session");
  });
});

describe("the generated reference", () => {
  it("is what's committed (make verify-skill regenerates it)", () => {
    const committed = readFileSync(join(c.ROOT, ".claude/skills/verify-runway/references/cli.md"), "utf8");
    expect(committed).toBe(c.markdownReference());
  });
});
