// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, forgetReplies, newPage } from "./api";

const reply = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status }));
let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => { fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock); });
afterEach(() => vi.unstubAllGlobals());

describe("api", () => {
  it("GETs JSON without the CSRF header", async () => {
    fetchMock.mockReturnValue(reply({ a: 1 }));
    expect(await api("/api/x")).toEqual({ a: 1 });
    const init = fetchMock.mock.calls[0][1];
    expect(init.method).toBe("GET");
    expect(init.headers).not.toHaveProperty("X-Runway");
  });

  it("sends state changes with the CSRF header and a JSON body", async () => {
    fetchMock.mockReturnValue(reply({ ok: true }));
    await api("/api/x", { method: "POST", body: { n: 2 } });
    const init = fetchMock.mock.calls[0][1];
    expect(init.headers).toEqual({ "X-Runway": "1", "Content-Type": "application/json" });
    expect(init.body).toBe('{"n":2}');
  });

  it("throws the server's error message with the status", async () => {
    fetchMock.mockReturnValue(reply({ error: "Nope" }, 400));
    const err = await api("/api/x").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ message: "Nope", status: 400 });
  });

  it("falls back to a generic message when the error reply isn't JSON", async () => {
    fetchMock.mockReturnValue(Promise.resolve(new Response("<html>Teapot</html>", { status: 418 })));
    await expect(api("/api/x")).rejects.toMatchObject({ message: "Request failed (418)", status: 418 });
  });

  it("says Runway is restarting when a proxy answers for it", async () => {
    for (const status of [502, 503, 504]) {
      fetchMock.mockReturnValue(Promise.resolve(new Response("<html>Bad gateway</html>", { status })));
      await expect(api("/api/x")).rejects.toMatchObject({ message: "Runway is restarting or unreachable. Try again in a moment.", status });
    }
    fetchMock.mockReturnValue(reply({ error: "Plaid is down" }, 502));
    await expect(api("/api/x")).rejects.toMatchObject({ message: "Plaid is down", status: 502 });
  });

  it("sends a file as it is, not as JSON, with the CSRF header", async () => {
    fetchMock.mockReturnValue(reply({ ok: true }));
    const file = new File([new Uint8Array([0x1f, 0x8b])], "backup.gz");
    await api("/api/restore", { method: "POST", body: file });
    const init = fetchMock.mock.calls[0][1];
    expect(init.headers).toEqual({ "X-Runway": "1", "Content-Type": "application/octet-stream" });
    expect(init.body).toBe(file);
  });

  it("names a refusal that says nothing with `failed`, and still shows the server's own words", async () => {
    fetchMock.mockReturnValue(Promise.resolve(new Response("<html>Oops</html>", { status: 500 })));
    await expect(api("/api/restore", { method: "POST", body: {}, failed: "Restore failed" })).rejects.toMatchObject({ message: "Restore failed (500)", status: 500 });
    fetchMock.mockReturnValue(reply({ error: "That file isn't a Runway backup." }, 400));
    await expect(api("/api/restore", { method: "POST", body: {}, failed: "Restore failed" })).rejects.toMatchObject({ message: "That file isn't a Runway backup." });
  });

  it("sends you to sign in again, and back here, when the session has expired", async () => {
    fetchMock.mockReturnValue(reply({}, 401));
    const fake = { href: "", pathname: "/", hash: "#budget" };
    vi.stubGlobal("location", fake);
    await expect(api("/api/x")).rejects.toMatchObject({ status: 401 });
    expect(fake.href).toBe("/auth/login?next=" + encodeURIComponent("/#budget"));
  });

  it("tells the app first, and stays on the page when Runway was only checking in or the app says so", async () => {
    fetchMock.mockImplementation(() => reply({}, 401));
    const fake = { href: "", pathname: "/", hash: "#budget" };
    vi.stubGlobal("location", fake);
    const seen: boolean[] = [];
    const listener = (e: CustomEvent<{ background: boolean }>) => { seen.push(e.detail.background); };
    window.addEventListener("runway:signed-out", listener);
    await expect(api("/api/state", { keep: true, background: true }))
      .rejects.toMatchObject({ message: "Your session expired. Sign in again to keep going.", status: 401 });
    expect(fake.href).toBe("");
    const cancel = (e: Event) => e.preventDefault();
    window.addEventListener("runway:signed-out", cancel);
    await expect(api("/api/x", { method: "POST", body: { split: 1 } })).rejects.toMatchObject({ status: 401 });
    expect(fake.href).toBe("");
    expect(seen).toEqual([true, false]);
    window.removeEventListener("runway:signed-out", listener);
    window.removeEventListener("runway:signed-out", cancel);
  });

  it("never answers a read the page moved on from", async () => {
    let release!: (r: Response) => void;
    fetchMock.mockReturnValue(new Promise<Response>((r) => { release = r; }));
    const settled = vi.fn();
    api("/api/slow").then(settled, settled);
    newPage();
    release(new Response("{}"));
    await new Promise((r) => setTimeout(r, 10));
    expect(settled).not.toHaveBeenCalled();
  });

  it("lets a read with keep finish even after the page changed", async () => {
    let release!: (r: Response) => void;
    fetchMock.mockReturnValue(new Promise<Response>((r) => { release = r; }));
    const p = api("/api/state", { keep: true });
    newPage();
    release(new Response('{"ok":1}'));
    expect(await p).toEqual({ ok: 1 });
    expect(fetchMock.mock.calls[0][1].signal).toBeUndefined();
  });

  it("does not cancel writes when the page changes", async () => {
    fetchMock.mockReturnValue(reply({ done: true }));
    const p = api("/api/x", { method: "POST" });
    newPage();
    expect(await p).toEqual({ done: true });
  });

  it("says Runway can't be reached, in the same words in every browser, when the page hasn't moved", async () => {
    for (const message of ["Failed to fetch", "Load failed", "NetworkError when attempting to fetch resource."]) {
      fetchMock.mockRejectedValue(new TypeError(message));
      await expect(api("/api/x")).rejects.toMatchObject({ message: "Can’t reach Runway. Check your connection and try again.", status: 0 });
    }
  });

  it("never answers a read the page moved on from, even when it failed", async () => {
    let fail!: (e: Error) => void;
    fetchMock.mockReturnValue(new Promise<Response>((_, r) => { fail = r; }));
    const settled = vi.fn();
    api("/api/slow").then(settled, settled);
    newPage();
    fail(new TypeError("Failed to fetch"));
    await new Promise((r) => setTimeout(r, 10));
    expect(settled).not.toHaveBeenCalled();
  });
});

describe("the app lock", () => {
  afterEach(() => { Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true }); });

  it("tells the app when this device's lock is locked (423), and says so", async () => {
    const locked = vi.fn();
    window.addEventListener("runway:locked", locked);
    fetchMock.mockReturnValue(reply({ error: "Runway is locked on this device.", locked: true }, 423));
    await expect(api("/api/x", { keep: true })).rejects.toMatchObject({ status: 423 });
    expect(locked).toHaveBeenCalledOnce();
    window.removeEventListener("runway:locked", locked);
  });

  it("marks what it asks from the background, which doesn't keep an unlock going, and can outlive the page", async () => {
    fetchMock.mockReturnValue(reply({}));
    await api("/api/x");
    expect(fetchMock.mock.calls[0][1].headers).not.toHaveProperty("X-Runway-Hidden");
    Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true });
    await api("/api/lock/engage", { method: "POST", keepalive: true });
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ keepalive: true, headers: { "X-Runway-Hidden": "1", "X-Runway": "1" } });
  });
});

describe("replies kept in memory (ETag)", () => {
  const tagged = (body: unknown, etag: string) => Promise.resolve(new Response(JSON.stringify(body), { headers: { ETag: etag } }));
  const unchanged = (etag: string) => Promise.resolve(new Response(null, { status: 304, headers: { ETag: etag } }));
  const asked = (n: number) => (fetchMock.mock.calls[n][1].headers as Record<string, string>)["If-None-Match"];
  afterEach(() => forgetReplies());

  it("asks with the last reply's ETag, and uses its copy on a 304, a fresh object each time", async () => {
    fetchMock.mockReturnValueOnce(tagged({ items: [1] }, '"a"'));
    const first = await api<{ items: number[] }>("/api/x");
    expect(asked(0)).toBeUndefined();
    first.items.push(2);   // a page changing what it was given doesn't change the copy
    fetchMock.mockReturnValueOnce(unchanged('"a"'));
    expect(await api("/api/x")).toEqual({ items: [1] });
    expect(asked(1)).toBe('"a"');
    fetchMock.mockReturnValueOnce(tagged({ items: [3] }, 'W/"b"'));   // changed: the new reply, and its ETag next time
    expect(await api("/api/x")).toEqual({ items: [3] });
    fetchMock.mockReturnValueOnce(unchanged('W/"b"'));
    expect(await api("/api/x")).toEqual({ items: [3] });
    expect(asked(3)).toBe('W/"b"');
  });

  it("keeps a copy per address, of reads only, and none of a reply without an ETag or a refusal", async () => {
    fetchMock.mockReturnValueOnce(tagged({ a: 1 }, '"a"'));
    await api("/api/x?q=1");
    fetchMock.mockReturnValue(reply({ ok: true }));
    await api("/api/x", { method: "POST" });
    await api("/api/x?q=2");
    expect(fetchMock.mock.calls.slice(1).map(([, init]) => init.headers["If-None-Match"])).toEqual([undefined, undefined]);
    await api("/api/x?q=1");   // its copy is gone: replaced by a reply without one
    await api("/api/x?q=1");
    expect(asked(4)).toBeUndefined();
    fetchMock.mockReturnValueOnce(tagged({ a: 1 }, '"a"')).mockReturnValueOnce(reply({ error: "Nope" }, 500)).mockReturnValueOnce(reply({}));
    await api("/api/y");
    await expect(api("/api/y")).rejects.toMatchObject({ status: 500 });
    await api("/api/y");
    expect([asked(6), asked(7)]).toEqual(['"a"', undefined]);
  });

  it("forgets them all on signing out, a 401 and a 423", async () => {
    vi.stubGlobal("location", { href: "", pathname: "/", hash: "" });
    const forgets: [string, () => Promise<unknown>][] = [
      ["signing out", async () => forgetReplies()],
      ["a 401", () => { fetchMock.mockReturnValueOnce(reply({}, 401)); return api("/api/other").catch(() => { /* the refusal is what this test is after */ }); }],
      ["a 423", () => { fetchMock.mockReturnValueOnce(reply({}, 423)); return api("/api/other", { keep: true }).catch(() => { /* the refusal is what this test is after */ }); }],
    ];
    for (const [what, forget] of forgets) {
      fetchMock.mockReset();
      fetchMock.mockReturnValueOnce(tagged({ a: 1 }, '"a"'));
      await api("/api/x");
      await forget();
      fetchMock.mockReturnValueOnce(tagged({ a: 2 }, '"b"'));
      expect(await api("/api/x"), what).toEqual({ a: 2 });
      expect(fetchMock.mock.calls.at(-1)![1].headers["If-None-Match"], what).toBeUndefined();
    }
  });

  it("asks again for the whole reply when a 304 answers a copy forgotten meanwhile", async () => {
    fetchMock.mockReturnValueOnce(tagged({ a: 1 }, '"a"'));
    await api("/api/x");
    let release!: (r: Response) => void;
    fetchMock.mockReturnValueOnce(new Promise<Response>((r) => { release = r; })).mockReturnValueOnce(tagged({ a: 2 }, '"b"'));
    const p = api("/api/x");
    forgetReplies();   // signed out while it was on its way
    release(new Response(null, { status: 304, headers: { ETag: '"a"' } }));
    expect(await p).toEqual({ a: 2 });
    expect([asked(1), asked(2)]).toEqual(['"a"', undefined]);
  });

  it("treats a 304 it didn't ask for as a failure", async () => {
    fetchMock.mockReturnValueOnce(unchanged('"a"'));
    await expect(api("/api/x")).rejects.toMatchObject({ status: 304 });
  });
});
