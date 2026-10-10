// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", async (orig) => ({ ...(await orig<typeof import("$lib/api")>()), api: vi.fn() }));

import { api, newPage } from "$lib/api";
import { route } from "$lib/app.svelte";
import type { Overview } from "$lib/types";
import { MAX_AGE_MS, overviewFor, prefetchOverview, takeEarlyOverview } from "$lib/prefetch";

// An Overview that covers `days` days (its dates run from today through the horizon).
const fc = (days: number) => ({ dates: Array.from({ length: days + 1 }, (_, i) => String(i)) }) as unknown as Overview;

beforeEach(() => { vi.mocked(api).mockReset(); route.page = "overview"; takeEarlyOverview(); vi.useFakeTimers(); });
afterEach(() => vi.useRealTimers());

describe("prefetchOverview", () => {
  it("asks for the forecast without a length, so the server uses the horizon it has", async () => {
    vi.mocked(api).mockResolvedValue(fc(90));
    prefetchOverview();
    expect(vi.mocked(api).mock.calls).toEqual([["/api/overview"]]);
    expect(await takeEarlyOverview()).toEqual(fc(90));
    expect(takeEarlyOverview()).toBeNull();   // used once
  });

  it("does nothing when another page is the one opening", () => {
    route.page = "transactions";
    prefetchOverview();
    expect(api).not.toHaveBeenCalled();
    expect(takeEarlyOverview()).toBeNull();
  });

  it("is dropped once the page changed (that read was cancelled and would never answer)", () => {
    vi.mocked(api).mockReturnValue(new Promise(() => {}));
    prefetchOverview();
    newPage();
    expect(takeEarlyOverview()).toBeNull();
  });

  it("is dropped when it has waited too long", () => {
    vi.mocked(api).mockResolvedValue(fc(90));
    prefetchOverview();
    vi.advanceTimersByTime(MAX_AGE_MS + 1);
    expect(takeEarlyOverview()).toBeNull();
  });

  it("a failure nobody asked about is not left unhandled", async () => {
    const unhandled = vi.fn();
    process.on("unhandledRejection", unhandled);
    vi.mocked(api).mockRejectedValue(new Error("boom"));
    prefetchOverview();
    await vi.advanceTimersByTimeAsync(10);
    process.off("unhandledRejection", unhandled);
    expect(unhandled).not.toHaveBeenCalled();
  });
});

describe("overviewFor", () => {
  it("uses the early answer when it covers the horizon asked for, with no further request", async () => {
    expect(await overviewFor(90, Promise.resolve(fc(90)))).toEqual(fc(90));
    expect(api).not.toHaveBeenCalled();
  });

  it("asks again, with the length, when the horizon isn't the one the early answer used", async () => {
    vi.mocked(api).mockResolvedValue(fc(180));
    expect(await overviewFor(180, Promise.resolve(fc(90)))).toEqual(fc(180));
    expect(vi.mocked(api).mock.calls).toEqual([["/api/overview?days=180"]]);
  });

  it("asks again when the early request failed", async () => {
    vi.mocked(api).mockResolvedValue(fc(90));
    expect(await overviewFor(90, Promise.reject(new Error("boom")))).toEqual(fc(90));
    expect(vi.mocked(api).mock.calls).toEqual([["/api/overview?days=90"]]);
  });

  it("asks the usual way with no early answer, and passes on its failure", async () => {
    vi.mocked(api).mockRejectedValue(new Error("down"));
    await expect(overviewFor(90, null)).rejects.toThrow("down");
    expect(vi.mocked(api).mock.calls).toEqual([["/api/overview?days=90"]]);
  });
});
