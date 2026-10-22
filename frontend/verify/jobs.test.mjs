import { existsSync, mkdtempSync, readdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { createServer } from "node:http";
import { answers, freePort, itemCost, listensOn, notRun, queueOrder, runPool, startServer } from "./jobs.mjs";

const flow = (name, steps = 3, extra = {}) => ({ id: `flow:${name}`, flow: { name, steps: Array.from({ length: steps }, () => ({ click: "a" })), ...extra } });
const page = (name) => ({ id: `page:${name}`, page: name });
const items = [page("overview"), page("budget"), flow("a", 9), flow("b", 2), flow("c", 5, { viewports: ["desktop"] }), { id: "device-cache" }];
const ids = (list) => list.map((i) => i.id);
const ok = (item, context, worker) => ({ problems: [], ranOn: `${context.name}/${worker}` });
const tick = () => new Promise((r) => setTimeout(r, 0));

describe("itemCost", () => {
  it("counts each width's visit and its steps; a page is a visit and two captures at each width", () => {
    expect(itemCost(page("budget"))).toBe(6);
    expect(itemCost(flow("a", 9))).toBe(20);
    expect(itemCost(flow("c", 5, { viewports: ["desktop"] }))).toBe(6);
    expect(itemCost({ id: "device-cache" })).toBe(40);
  });
});

describe("queueOrder", () => {
  it("keeps the planned order for one worker, numbering each item by its place in the plan", () => {
    const queue = queueOrder(items, 1);
    expect(ids(queue)).toEqual(ids(items));
    expect(queue.map((i) => i.index)).toEqual([0, 1, 2, 3, 4, 5]);
  });

  it("puts the costliest first for several workers, ties in planned order, the same every time", () => {
    const queue = queueOrder(items, 4);
    expect(ids(queue)).toEqual(["device-cache", "flow:a", "page:overview", "page:budget", "flow:b", "flow:c"]);
    expect(ids(queueOrder(items, 4))).toEqual(ids(queue));
  });

  it("turns either order around when asked (make verify-twice's second run)", () => {
    expect(ids(queueOrder(items, 1, { reverse: true }))).toEqual(ids(items).reverse());
    expect(ids(queueOrder(items, 3, { reverse: true }))).toEqual(ids(queueOrder(items, 3)).reverse());
  });

  it("leaves the items it was given as they were", () => {
    const copy = structuredClone(items);
    queueOrder(items, 3, { reverse: true });
    expect(items).toEqual(copy);
  });
});

describe("runPool", () => {
  it("runs every item exactly once and returns the outcomes in planned order, whatever order they finished in", async () => {
    const ran = [];
    const { outcomes, failures } = await runPool({
      items, workers: 3,
      prepare: async (worker) => ({ name: `browser${worker}` }),
      // The costly ones take longest: they finish last though they started first.
      work: async (item, context, worker) => { for (let i = 0; i < itemCost(item); i++) await tick(); ran.push(item.id); return ok(item, context, worker); },
    });
    expect(failures).toEqual([]);
    expect(ran.slice().sort()).toEqual(ids(items).sort());
    expect(ids(outcomes.map((o) => o.item))).toEqual(ids(items));
    expect(outcomes.every((o) => o.ranOn === `browser${o.worker}/${o.worker}`)).toBe(true);
    expect(new Set(outcomes.map((o) => o.worker)).size).toBe(3);   // shared out, not all on one
  });

  it("runs at most `workers` items at once, and never more workers than items", async () => {
    let running = 0, most = 0;
    const prepared = [];
    await runPool({
      items, workers: 4,
      prepare: async (worker) => { prepared.push(worker); return { name: "b" }; },
      work: async (item, context, worker) => { running++; most = Math.max(most, running); await tick(); await tick(); running--; return ok(item, context, worker); },
    });
    expect(most).toBe(4);
    const few = [];
    await runPool({ items: items.slice(0, 2), workers: 4, prepare: async (worker) => { few.push(worker); return { name: "b" }; }, work: ok });
    expect(few.sort()).toEqual([0, 1]);
  });

  it("with one worker runs them one after the other, in the planned order", async () => {
    const seen = [];
    await runPool({ items, workers: 1, prepare: async () => ({ name: "only" }), work: async (item, context) => { seen.push(item.id); return ok(item, context, 0); } });
    expect(seen).toEqual(ids(items));
  });

  it("lets the other workers take the share of one that can't start, and says why it couldn't", async () => {
    const { outcomes, failures } = await runPool({
      items, workers: 2,
      prepare: async (worker) => { if (worker === 0) throw new Error("browser missing\nstack"); return { name: "b1" }; },
      work: ok,
    });
    expect(failures).toEqual([{ worker: 0, reason: "couldn't start: browser missing" }]);
    expect(outcomes).toHaveLength(items.length);
    expect(outcomes.every((o) => o.worker === 1 && !o.problems.length)).toBe(true);
  });

  it("marks every item not run, with the reason, when no worker can start", async () => {
    const { outcomes, failures } = await runPool({ items, workers: 2, prepare: async () => { throw new Error("no browser"); }, work: ok });
    expect(failures).toEqual([{ worker: 0, reason: "couldn't start: no browser" }, { worker: 1, reason: "couldn't start: no browser" }]);
    expect(ids(outcomes.map((o) => o.item))).toEqual(ids(items));
    for (const o of outcomes) {
      expect(o.worker).toBeNull();
      expect(o.problems).toEqual(["not run: no worker could take it (worker 1 couldn't start: no browser; worker 2 couldn't start: no browser)"]);
    }
  });

  it("fails the item a worker stopped on, with the reason, and lets the others finish the rest", async () => {
    const { outcomes, failures } = await runPool({
      items, workers: 2,
      prepare: async (worker) => ({ name: `b${worker}` }),
      work: async (item, context, worker) => { await tick(); if (item.id === "flow:a") throw new Error("browser gone"); return ok(item, context, worker); },
    });
    const failed = outcomes.filter((o) => o.problems.length);
    expect(ids(failed.map((o) => o.item))).toEqual(["flow:a"]);
    const stopped = failed[0].worker;
    expect(failures).toEqual([{ worker: stopped, reason: "stopped: browser gone" }]);
    expect(failed[0].problems).toEqual([`not run: worker ${stopped + 1} stopped (browser gone)`]);
    expect(outcomes).toHaveLength(items.length);
    expect(outcomes.filter((o) => o.item.id !== "flow:a" && o.item.id !== "device-cache").every((o) => o.worker === 1 - stopped)).toBe(true);
  });

  it("lets every worker's browser go, and says when that fails", async () => {
    const released = [];
    const { failures } = await runPool({
      items, workers: 2, prepare: async (worker) => ({ name: `b${worker}` }), work: ok,
      release: async (context, worker) => { released.push(context.name); if (worker === 1) throw new Error("already closed"); },
    });
    expect(released.sort()).toEqual(["b0", "b1"]);
    expect(failures).toEqual([{ worker: 1, reason: "closing: already closed" }]);
  });

  it("describes an item that never ran", () => {
    expect(notRun(page("budget"), "the run ended first")).toEqual({ item: page("budget"), worker: null, problems: ["not run: the run ended first"] });
  });
});

describe("startServer", () => {
  // A stand-in for runway/verify.py's `serve`: makes its folder and log, then answers on the port, exits, or sits silent.
  const fake = (mode) => (port, dir) => [process.execPath, "-e", `
    const fs = require("node:fs"); fs.mkdirSync(process.argv[2]); fs.writeFileSync(process.argv[2] + "/server.log", "listening on " + process.argv[1] + "\\n");
    if (${JSON.stringify(mode)} === "exit") process.exit(3);
    if (${JSON.stringify(mode)} === "ok") require("node:http").createServer((q, r) => r.end("ok")).listen(Number(process.argv[1]), "127.0.0.1");
    else setInterval(() => {}, 1000);`, String(port), dir];
  const counting = (ports) => async () => { const p = await freePort(); ports.push(p); return p; };
  let root;
  beforeEach(() => { root = mkdtempSync(join(tmpdir(), "verify-jobs-")); });
  afterEach(() => rmSync(root, { recursive: true, force: true }));

  it("starts a server in a folder of its own, waits until it answers, and stopping it removes the folder", async () => {
    const server = await startServer({ command: fake("ok"), folder: join(root, "s1"), timeout: 10000, poll: 20 });
    expect(await answers(server.port, "/")).toBe(true);
    expect(existsSync(join(root, "s1-1", "server.log"))).toBe(true);
    await server.stop();
    expect(existsSync(join(root, "s1-1"))).toBe(false);
    expect(await answers(server.port, "/")).toBe(false);
  });

  it("tries again on another port when the server exits before it answers (another process took the port), then gives up with its log", async () => {
    const ports = [];
    const failed = startServer({ command: fake("exit"), folder: join(root, "s"), timeout: 10000, poll: 20, port: counting(ports) });
    await expect(failed).rejects.toThrow(/the demo server didn't start: it exited with 3 before it answered \(3 tries\)\nlistening on \d+/);
    expect(ports).toHaveLength(3);
    expect(readdirSync(root)).toEqual([]);   // no folder left behind
  });

  it("stops a server that never answers, and says on which port and for how long it waited", async () => {
    const failed = startServer({ command: fake("silent"), folder: join(root, "s"), timeout: 300, poll: 20 });
    await expect(failed).rejects.toThrow(/wasn't answering on port \d+ after 0 seconds\nlistening on/);
    expect(readdirSync(root)).toEqual([]);
  });

  it("never takes another process's server on the port for its own (it would have another item's data)", async () => {
    const foreign = createServer((q, r) => r.end("someone else's"));
    await new Promise((r) => foreign.listen(0, "127.0.0.1", r));
    const { port } = foreign.address();
    try {
      expect(listensOn(process.pid, port)).toBe(true);   // this test process holds it
      const failed = startServer({ command: fake("silent"), folder: join(root, "s"), timeout: 500, poll: 20, port: async () => port });
      await expect(failed).rejects.toThrow(/wasn't answering on port/);
    } finally {
      await new Promise((r) => foreign.close(r));
    }
    expect(listensOn(process.pid, port)).toBe(false);
  });

  it("says when the program can't be started at all, without trying again", async () => {
    const ports = [];
    await expect(startServer({ command: () => [join(root, "no-such-program")], folder: join(root, "s"), timeout: 5000, poll: 20, port: counting(ports) }))
      .rejects.toThrow(/it couldn't start \(spawn .*ENOENT\)/);
    expect(ports).toHaveLength(1);
  });
});
