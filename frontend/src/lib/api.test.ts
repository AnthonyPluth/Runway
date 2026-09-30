// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, newPage } from "./api";

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
    fetchMock.mockReturnValue(reply({ error: "Plaid is down" }, 502));   // Runway's own message stays as it is
    await expect(api("/api/x")).rejects.toMatchObject({ message: "Plaid is down", status: 502 });
  });

  it("sends you to sign in again, and back here, when the session has expired", async () => {
    fetchMock.mockReturnValue(reply({}, 401));
    const fake = { href: "", pathname: "/", hash: "#budget" };   // jsdom can't navigate, so watch the assignment
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
    const cancel = (e: Event) => e.preventDefault();   // what the app does while you're editing
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
