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
    fetchMock.mockReturnValue(Promise.resolve(new Response("<html>Bad gateway</html>", { status: 502 })));
    await expect(api("/api/x")).rejects.toMatchObject({ message: "Request failed (502)", status: 502 });
  });

  it("sends you to sign in again, and back here, when the session has expired", async () => {
    fetchMock.mockReturnValue(reply({}, 401));
    const fake = { href: "", pathname: "/", hash: "#budget" };   // jsdom can't navigate, so watch the assignment
    vi.stubGlobal("location", fake);
    await expect(api("/api/x")).rejects.toMatchObject({ status: 401 });
    expect(fake.href).toBe("/auth/login?next=" + encodeURIComponent("/#budget"));
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

  it("passes a network failure through when the page hasn't moved", async () => {
    fetchMock.mockRejectedValue(new TypeError("offline"));
    await expect(api("/api/x")).rejects.toThrow("offline");
  });
});
