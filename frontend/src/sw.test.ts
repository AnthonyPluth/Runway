// The service worker (runway/static/sw.js) is a plain script, so it runs here in a VM with a fake `self`, `caches` and
// `fetch`: the tests send it fetch events and see what it answers and what it keeps.
import { readFileSync } from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { beforeEach, describe, expect, it, vi } from "vitest";

const ORIGIN = "https://runway.test";
const SOURCE = readFileSync(path.resolve(__dirname, "../../runway/static/sw.js"), "utf8");

type Listener = (event: unknown) => void;
type Req = { method: string; url: string; mode: string; headers: Headers };
const req = (p: string, over: Partial<Req> = {}, origin = ORIGIN): Req =>
  ({ method: "GET", url: origin + p, mode: "no-cors", headers: new Headers(), ...over });

/** A Response with the properties a real one has from fetch (`redirected` and `type` can't be set on `new Response`). */
const res = (body: string, over: { status?: number; type?: string; redirected?: boolean; contentType?: string } = {}) => {
  const r = new Response(body, { status: over.status && over.status >= 200 ? over.status : 200, headers: over.contentType ? { "Content-Type": over.contentType } : {} });
  Object.defineProperty(r, "type", { value: over.type ?? "basic" });
  Object.defineProperty(r, "redirected", { value: over.redirected ?? false });
  if (over.status === 0) Object.defineProperty(r, "status", { value: 0 });   // an opaque redirect, as a worker sees it
  return r;
};
const JS = { contentType: "text/javascript" };

function boot() {
  const stores = new Map<string, Map<string, Response>>();
  const store = (name: string) => { if (!stores.has(name)) stores.set(name, new Map()); return stores.get(name)!; };
  const cacheOf = (name: string) => {
    const m = store(name);
    return {
      match: async (key: string) => m.get(key)?.clone(),
      put: async (key: string, r: Response) => { m.delete(key); m.set(key, r); },   // a re-put goes last, as in a real cache
      delete: async (key: string | { url: string }) => m.delete(typeof key === "string" ? key : new URL(key.url).pathname),
      keys: async () => [...m.keys()].map((k) => ({ url: ORIGIN + k })),
      addAll: async () => {},
    };
  };
  const listeners: Record<string, Listener> = {};
  const network = vi.fn<(r: Req) => Promise<Response>>();
  const self = { addEventListener: (t: string, l: Listener) => { listeners[t] = l; }, skipWaiting: vi.fn(), clients: { claim: vi.fn() }, registration: {} };
  const caches = {
    open: async (name: string) => cacheOf(name),
    keys: async () => [...stores.keys()],
    delete: async (name: string) => stores.delete(name),
    match: async (key: string) => { for (const m of stores.values()) if (m.has(key)) return m.get(key)!.clone(); return undefined; },
  };
  const ctx = vm.createContext({ self, caches, fetch: network, location: { origin: ORIGIN }, URL, Headers, Response, console });
  vm.runInContext(SOURCE, ctx);

  /** Send a fetch event: what the worker answered (undefined if it left the request to the browser) once it settled. */
  async function send(r: Req) {
    let answer: Promise<Response> | undefined;
    const pending: Promise<unknown>[] = [];
    listeners.fetch({ request: r, respondWith: (p: Promise<Response>) => { answer = p; }, waitUntil: (p: Promise<unknown>) => { pending.push(p); } });
    const out = answer && (await answer.then((x) => ({ ok: x }), (e) => ({ err: e })));
    await Promise.all(pending);
    return { handled: answer !== undefined, out };
  }
  return { ctx, listeners, stores, store, network, send, self };
}

let sw: ReturnType<typeof boot>;
beforeEach(() => { sw = boot(); });

describe("which requests the worker takes", () => {
  const route = (r: Req) => (sw.ctx as { route: (r: Req, u: URL, o: string) => string | null }).route(r, new URL(r.url), ORIGIN);

  it("answers built files from the kept copy first", () => {
    expect(route(req("/assets/index-abc123.js"))).toBe("asset");
    expect(route(req("/assets/index-abc123.css"))).toBe("asset");
  });
  it("keeps the app's page, shell files and fonts network first", () => {
    expect(route(req("/", { mode: "navigate" }))).toBe("shell");
    expect(route(req("/plaid/oauth", { mode: "navigate" }))).toBe("shell");
    expect(route(req("/fonts/Inter-latin-Variable.woff2"))).toBe("shell");
    expect(route(req("/manifest.webmanifest"))).toBe("shell");
  });
  it("leaves the API, sign-in, other pages, other origins, other methods and partial reads alone", () => {
    expect(route(req("/api/state"))).toBeNull();
    expect(route(req("/api/overview?days=90"))).toBeNull();
    expect(route(req("/auth/login", { mode: "navigate" }))).toBeNull();
    expect(route(req("/auth/login"))).toBeNull();
    expect(route(req("/oauth/authorize", { mode: "navigate" }))).toBeNull();
    expect(route(req("/assets/index-abc123.js", {}, "https://elsewhere.test"))).toBeNull();
    expect(route(req("/assets/index-abc123.js", { method: "POST" }))).toBeNull();
    expect(route(req("/assets/video.mp4", { headers: new Headers({ Range: "bytes=0-9" }) }))).toBeNull();
    expect(route(req("/assets/index-abc123.js", { mode: "navigate" }))).toBeNull();   // a link straight to a script is no page of the app
    expect(route(req("/icon-192.png"))).toBeNull();
  });
  it("never takes /api or /auth even when written to look like an asset", () => {
    expect(route(req("/api/assets/x.js"))).toBeNull();
    expect(route(req("/auth/assets/x.js"))).toBeNull();
  });
});

describe("built files (/assets/…)", () => {
  it("come from the kept copy with no request to the server", async () => {
    await (await sw.ctx.caches.open("runway-shell-v6")).put("/assets/a.js", res("kept", JS));
    const { out } = await sw.send(req("/assets/a.js"));
    expect(await (out as { ok: Response }).ok.text()).toBe("kept");
    expect(sw.network).not.toHaveBeenCalled();
  });

  it("not kept yet are fetched, answered and kept for next time", async () => {
    sw.network.mockResolvedValue(res("fresh", JS));
    const first = await sw.send(req("/assets/a.js"));
    expect(await (first.out as { ok: Response }).ok.text()).toBe("fresh");
    expect([...sw.store("runway-shell-v6").keys()]).toEqual(["/assets/a.js"]);
    await sw.send(req("/assets/a.js"));
    expect(sw.network).toHaveBeenCalledTimes(1);
  });

  it("keys the copy by its path, so a query string doesn't make another copy", async () => {
    sw.network.mockResolvedValue(res("x", JS));
    await sw.send(req("/assets/a.js?v=1"));
    await sw.send(req("/assets/a.js?v=2"));
    expect(sw.network).toHaveBeenCalledTimes(1);
  });

  it("don't keep a followed redirect (a signed-out reply is the sign-in page, not the file)", async () => {
    sw.network.mockResolvedValue(res("<html>Sign in</html>", { redirected: true, contentType: "text/html" }));
    const { out } = await sw.send(req("/assets/a.js"));
    expect(await (out as { ok: Response }).ok.text()).toContain("Sign in");   // still shown to the page
    expect(sw.store("runway-shell-v6").size).toBe(0);
    // even if what came back looked like a script
    sw.network.mockResolvedValue(res("x", { redirected: true, ...JS }));
    await sw.send(req("/assets/b.js"));
    expect(sw.store("runway-shell-v6").size).toBe(0);
  });

  it("don't keep an opaque redirect, an opaque answer, an error or a partial answer", async () => {
    for (const r of [res("", { status: 0, type: "opaqueredirect" }), res("x", { type: "opaque", ...JS }), res("no", { status: 404, ...JS }),
      res("no", { status: 500, ...JS }), res("part", { status: 206, ...JS }), res("cors", { type: "cors", ...JS })]) {
      sw.network.mockResolvedValue(r);
      await sw.send(req("/assets/a.js"));
    }
    expect(sw.store("runway-shell-v6").size).toBe(0);
  });

  it("don't keep a web page: an address the app doesn't have answers with the app's page", async () => {
    sw.network.mockResolvedValue(res("<html>app with a nonce</html>", { contentType: "text/html; charset=utf-8" }));
    await sw.send(req("/assets/gone-abc.js"));
    expect(sw.store("runway-shell-v6").size).toBe(0);
  });

  it("fail when offline and not kept (nothing else to show)", async () => {
    sw.network.mockRejectedValue(new TypeError("offline"));
    const { out } = await sw.send(req("/assets/a.js"));
    expect((out as { err: Error }).err).toBeInstanceOf(TypeError);
  });

  it("keep only the most recent 150", async () => {
    sw.network.mockImplementation(async () => res("x", JS));
    for (let i = 0; i < 152; i++) await sw.send(req(`/assets/f${i}.js`));
    const keys = [...sw.store("runway-shell-v6").keys()];
    expect(keys).toHaveLength(150);
    expect(keys[0]).toBe("/assets/f2.js");
    expect(keys.at(-1)).toBe("/assets/f151.js");
  });
});

describe("the app's page and shell files", () => {
  it("go to the server first, even when a copy is kept, and the answer is kept", async () => {
    await (await sw.ctx.caches.open("runway-shell-v6")).put("/", res("old", { contentType: "text/html" }));
    sw.network.mockResolvedValue(res("new", { contentType: "text/html; charset=utf-8" }));
    const { out } = await sw.send(req("/", { mode: "navigate" }));
    expect(await (out as { ok: Response }).ok.text()).toBe("new");
    expect(await (await sw.store("runway-shell-v6").get("/")!.text())).toBe("new");
  });

  it("fall back to the kept page only when the server can't be reached", async () => {
    await (await sw.ctx.caches.open("runway-shell-v6")).put("/", res("kept page", { contentType: "text/html" }));
    sw.network.mockRejectedValue(new TypeError("offline"));
    const { out } = await sw.send(req("/", { mode: "navigate" }));
    expect(await (out as { ok: Response }).ok.text()).toBe("kept page");
  });

  it("don't keep a sign-in redirect (followed or not), another user's or a signed-out reply as the page", async () => {
    await (await sw.ctx.caches.open("runway-shell-v6")).put("/", res("mine", { contentType: "text/html" }));
    for (const r of [res("", { status: 0, type: "opaqueredirect" }), res("<html>Sign in</html>", { redirected: true, contentType: "text/html" })]) {
      sw.network.mockResolvedValue(r);
      await sw.send(req("/", { mode: "navigate" }));
    }
    expect(await sw.store("runway-shell-v6").get("/")!.text()).toBe("mine");
  });

  it("don't keep a navigation that isn't a page", async () => {
    sw.network.mockResolvedValue(res("x", { contentType: "application/json" }));
    await sw.send(req("/", { mode: "navigate" }));
    expect(sw.store("runway-shell-v6").size).toBe(0);
  });
});

describe("the API and sign-in", () => {
  it("are never touched or kept", async () => {
    for (const p of ["/api/state", "/api/overview?days=90", "/auth/login", "/auth/logout"]) {
      expect((await sw.send(req(p))).handled).toBe(false);
      expect((await sw.send(req(p, { mode: "navigate" }))).handled).toBe(false);
    }
    expect(sw.network).not.toHaveBeenCalled();
    expect([...sw.stores.values()].every((m) => m.size === 0)).toBe(true);
  });
});

describe("versions", () => {
  it("names this version's cache v6", () => {
    expect(SOURCE).toContain('const CACHE = "runway-shell-v6"');
  });
  it("deletes every other cache when it takes over", async () => {
    sw.store("runway-shell-v5").set("/assets/old.js", res("old", JS));
    sw.store("runway-shell-v6").set("/assets/new.js", res("new", JS));
    let done: Promise<unknown> | undefined;
    sw.listeners.activate({ waitUntil: (p: Promise<unknown>) => { done = p; } });
    await done;
    expect([...sw.stores.keys()]).toEqual(["runway-shell-v6"]);
    expect(sw.self.clients.claim).toHaveBeenCalled();
  });
});

describe("notifications", () => {
  it("still has its push and click handlers", () => {
    expect(typeof sw.listeners.push).toBe("function");
    expect(typeof sw.listeners.notificationclick).toBe("function");
  });
});
