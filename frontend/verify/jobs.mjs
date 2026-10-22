// How `make verify` spreads its pages, flows and checks over workers (verify.mjs runs them; runway/verify.py says how
// many). Every item runs on a demo server and a copy of the demo data of its own, started for it and stopped after it,
// so no item sees what another changed: what an item shows is the same whichever worker runs it, in whatever order,
// alone (`PAGES="transactions-delete"`) or with every other item. A worker is a browser of its own; workers take the next
// item from one shared queue, the costliest first, so the run ends with the slow ones done rather than started last.
import { spawn } from "node:child_process";
import { existsSync, readdirSync, readFileSync, readlinkSync, rmSync } from "node:fs";
import { get } from "node:http";
import { createServer } from "node:net";

/** A port nobody on this machine is listening on just now (another process can still take it before the server does:
 *  startServer tries again on another one then). */
export const freePort = () => new Promise((resolve, reject) => {
  const probe = createServer();
  probe.on("error", reject);
  probe.listen(0, "127.0.0.1", () => { const { port } = probe.address(); probe.close(() => resolve(port)); });
});

/** Whether the server answers on the loopback address (straight to it: node:http never goes through a proxy). */
export const answers = (port, path = "/healthz") => new Promise((resolve) => {
  const req = get({ host: "127.0.0.1", port, path, timeout: 2000 }, (res) => { res.resume(); resolve(res.statusCode >= 200 && res.statusCode < 400); });
  req.on("error", () => resolve(false));
  req.on("timeout", () => { req.destroy(); resolve(false); });
});

/** Whether process `pid` itself holds the socket listening on `port` (Linux's /proc: the listening sockets in /proc/net/tcp
 *  and tcp6, against the process's open files). So an answer on the port is known to come from the server just started,
 *  not from another process that took the port first. null where /proc can't be read (not Linux). */
export function listensOn(pid, port, proc = "/proc") {
  let inodes;
  try {
    inodes = new Set(["tcp", "tcp6"].flatMap((f) => {
      try { return readFileSync(`${proc}/net/${f}`, "utf8").split("\n").slice(1); } catch { return []; /* no IPv6: no tcp6 */ }
    }).map((line) => line.trim().split(/\s+/)).filter((c) => c.length > 9 && c[3] === "0A" && parseInt(c[1].split(":")[1], 16) === port)
      .map((c) => `socket:[${c[9]}]`));
    if (!inodes.size) return false;
    return readdirSync(`${proc}/${pid}/fd`).some((fd) => {
      try { return inodes.has(readlinkSync(`${proc}/${pid}/fd/${fd}`)); } catch { return false; /* a file closed meanwhile */ }
    });
  } catch {
    return existsSync(`${proc}/net/tcp`) ? false : null;   /* its files gone (it ended): not it; no /proc at all: can't tell */
  }
}

const exited = (proc) => new Promise((resolve) => { if (proc.exitCode !== null || proc.signalCode !== null) resolve(); else proc.once("exit", () => resolve()); });
const tail = (file) => { try { return readFileSync(file, "utf8").slice(-2000); } catch { return ""; /* no log: it never got that far */ } };

/** Stops a server: SIGTERM, then SIGKILL after `grace` ms; then removes its folder (its copy of the data). */
async function stopServer(proc, folder, grace = 10000) {
  if (proc.exitCode === null && proc.signalCode === null) {
    proc.kill("SIGTERM");
    let timer;
    await Promise.race([exited(proc), new Promise((r) => { timer = setTimeout(r, grace); })]);
    clearTimeout(timer);
    if (proc.exitCode === null && proc.signalCode === null) { proc.kill("SIGKILL"); await exited(proc); }
  }
  rmSync(folder, { recursive: true, force: true });
}

/** Starts a demo server on a fresh copy of the seeded data in `folder` (which must not exist yet): `command(port, folder)`
 *  gives the program and its arguments (runway/verify.py's `serve`, which copies the data, writes the server's output to
 *  `<folder>/server.log` and runs it under the frozen clock). Waits up to `timeout` ms for it to answer. A server that
 *  exits before it answers (another process took the port) is tried again on another port, `tries` times in all; one
 *  that never answers is stopped and fails at once. Returns { port, stop() }, or throws with the end of its log. */
export async function startServer({ command, folder, timeout = 60000, tries = 3, cwd, poll = 200, port: pick = freePort }) {
  let why = "";
  for (let attempt = 1; attempt <= tries; attempt++) {
    const port = await pick();
    const dir = `${folder}-${attempt}`;
    const [program, ...args] = command(port, dir);
    const proc = spawn(program, args, { cwd, stdio: "ignore" });
    let failed = null;
    proc.once("error", (e) => { failed = e.message; });
    const end = Date.now() + timeout;
    for (;;) {
      if (failed || proc.exitCode !== null || proc.signalCode !== null) break;
      // Answering, from this process (not another that had the port first: its server would hold another item's data).
      if (await answers(port) && listensOn(proc.pid, port) !== false) return { port, stop: () => stopServer(proc, dir) };
      if (Date.now() > end) {
        const log = tail(`${dir}/server.log`);
        await stopServer(proc, dir);
        throw new Error(`the demo server wasn't answering on port ${port} after ${Math.round(timeout / 1000)} seconds\n${log}`);
      }
      await new Promise((r) => setTimeout(r, poll));
    }
    if (!failed) await exited(proc);   // (a program that couldn't be started never exits)
    why = failed ? `it couldn't start (${failed})` : `it exited with ${proc.exitCode ?? proc.signalCode} before it answered`;
    const log = tail(`${dir}/server.log`);
    rmSync(dir, { recursive: true, force: true });
    if (failed || attempt === tries) throw new Error(`the demo server didn't start: ${why}${tries > 1 && !failed ? ` (${attempt} tries)` : ""}\n${log}`);
  }
  throw new Error(`the demo server didn't start: ${why}`);
}

/** How long an item is likely to take, in rough units (a visit to a page or a flow's step, at one width), for ordering the
 *  queue: each width's visit plus its steps (a page: opening it and its two captures), the device cache check a few dozen. */
export function itemCost(item, defaultViewports = ["phone", "desktop"]) {
  if (item.page) return defaultViewports.length * 3;
  if (item.flow) return (item.flow.viewports ?? defaultViewports).length * (1 + item.flow.steps.length);
  return 40;
}

/** The order the workers take the items in: with one worker, the order they were planned in (as a serial run always
 *  went); with more, the costliest first, ties in planned order. `reverse` turns either around (make verify-twice's second
 *  run, so the check also covers the order). Each item carries its place in the plan as `index`. */
export function queueOrder(items, workers, { reverse = false, cost = itemCost } = {}) {
  const planned = items.map((item, index) => ({ ...item, index }));
  const ordered = workers > 1 ? [...planned].sort((a, b) => cost(b) - cost(a) || a.index - b.index) : planned;
  return reverse ? ordered.reverse() : ordered;
}

const firstLine = (e) => String(e?.message ?? e).split("\n")[0];

/** The result of an item that never ran: it fails, saying why. */
export const notRun = (item, reason) => ({ item, worker: null, problems: [`not run: ${reason}`] });

/** Runs `work(item, context, worker)` for every item, on `workers` workers at once. Each worker first makes its context
 *  (`prepare(worker)`: its browser) and lets it go at the end (`release`); a worker that can't start leaves its share to
 *  the others, and one whose work throws stops there (that item fails with the reason) while the others go on. What no
 *  worker could take is reported as not run, with the reason. Returns each item's outcome ({ item, worker, ...what work
 *  returned }) in planned order, and each worker's failure ({ worker, reason }), whatever order they finished in. */
export async function runPool({ items, workers, prepare, work, release = async () => {}, reverse = false, cost = itemCost }) {
  const count = Math.max(1, Math.min(workers, items.length || 1));
  const queue = queueOrder(items, count, { reverse, cost });
  const outcomes = new Map();
  const failures = [];
  await Promise.all(Array.from({ length: count }, async (_, worker) => {
    let context;
    try {
      context = await prepare(worker);
    } catch (e) {
      failures.push({ worker, reason: `couldn't start: ${firstLine(e)}` });
      return;
    }
    try {
      for (let item = queue.shift(); item; item = queue.shift()) {
        try {
          outcomes.set(item.index, { ...(await work(item, context, worker)), item, worker });
        } catch (e) {
          failures.push({ worker, reason: `stopped: ${firstLine(e)}` });
          outcomes.set(item.index, { ...notRun(item, `worker ${worker + 1} stopped (${firstLine(e)})`), worker });
          return;
        }
      }
    } finally {
      await release(context, worker).catch((e) => failures.push({ worker, reason: `closing: ${firstLine(e)}` }));
    }
  }));
  const why = failures.length ? failures.map((f) => `worker ${f.worker + 1} ${f.reason}`).join("; ") : "no worker took it";
  for (const item of queue) outcomes.set(item.index, notRun(item, `no worker could take it (${why})`));
  return {
    outcomes: [...outcomes.values()].sort((a, b) => a.item.index - b.item.index),
    failures: failures.sort((a, b) => a.worker - b.worker),
  };
}
