// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiCall } from "./contract";

const reply = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status }));
let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => { fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock); });
afterEach(() => vi.unstubAllGlobals());

describe("apiCall", () => {
  it("sends what api() does, for a route the contract covers", async () => {
    fetchMock.mockReturnValue(reply({ ok: true, raised: [] }));
    const r = await apiCall<"POST /api/budget">("/api/budget", { method: "POST", body: { category: "Groceries", amount: 500 } });
    expect(r.raised).toEqual([]);
    const init = fetchMock.mock.calls[0][1];
    expect(init.method).toBe("POST");
    expect(init.headers).toEqual({ "X-Runway": "1", "Content-Type": "application/json" });
    expect(init.body).toBe('{"category":"Groceries","amount":500}');
  });

  it("checks the address, the method, the body and the reply against the contract (npm run check)", async () => {
    fetchMock.mockImplementation(() => reply({}));
    const month = "2026-03";
    const b = await apiCall<"GET /api/budget">(`/api/budget?month=${month}`);
    expect(b.days_in_month satisfies number).toBeUndefined();
    // @ts-expect-error: not a field of the reply
    expect(b.days).toBeUndefined();
    await apiCall<"DELETE /api/transactions/{id}">(`/api/transactions/${encodeURIComponent("t 1")}`, { method: "DELETE" });
    // @ts-expect-error: the route's address, not another's
    await apiCall<"GET /api/budget">("/api/transactions");
    // @ts-expect-error: a POST route says so
    await apiCall<"POST /api/budget">("/api/budget", { body: { category: "Groceries" } });
    // @ts-expect-error: the body is the contract's (a category is needed)
    await apiCall<"POST /api/budget">("/api/budget", { method: "POST", body: { amount: 5 } });
    // @ts-expect-error: a GET sends no body
    await apiCall<"GET /api/accounts">("/api/accounts", { body: { a: 1 } });
    // @ts-expect-error: only the routes the contract covers
    await apiCall<"GET /api/nothing">("/api/nothing");
    expect(fetchMock).toHaveBeenCalledTimes(7);
  });
});
