import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { debounced, latestOnly } from "./debounce";

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("debounced", () => {
  it("runs once, with the last arguments, after the pause", () => {
    const fn = vi.fn(), d = debounced(fn, 300);
    d.call("a"); vi.advanceTimersByTime(200);
    d.call("b"); vi.advanceTimersByTime(299);
    expect(fn).not.toHaveBeenCalled();
    expect(d.pending).toBe(true);
    vi.advanceTimersByTime(1);
    expect(fn).toHaveBeenCalledExactlyOnceWith("b");
    expect(d.pending).toBe(false);
  });

  it("flush runs a waiting call now, once, and does nothing when none waits", () => {
    const fn = vi.fn(), d = debounced(fn, 700);
    d.flush();
    expect(fn).not.toHaveBeenCalled();
    d.call(1);
    d.flush();
    expect(fn).toHaveBeenCalledExactlyOnceWith(1);
    vi.advanceTimersByTime(1000);
    d.flush();
    expect(fn).toHaveBeenCalledOnce();
  });

  it("cancel drops a waiting call", () => {
    const fn = vi.fn(), d = debounced(fn, 250);
    d.call();
    d.cancel();
    expect(d.pending).toBe(false);
    vi.advanceTimersByTime(1000);
    expect(fn).not.toHaveBeenCalled();
  });

  it("can be called again after it has run", () => {
    const fn = vi.fn(), d = debounced(fn, 100);
    d.call(1); vi.advanceTimersByTime(100);
    d.call(2); vi.advanceTimersByTime(100);
    expect(fn.mock.calls).toEqual([[1], [2]]);
  });
});

describe("latestOnly", () => {
  it("is true only for the newest start", () => {
    const l = latestOnly();
    const first = l.begin(), second = l.begin();
    expect(first()).toBe(false);
    expect(second()).toBe(true);
  });

  it("cancel overtakes whatever is out", () => {
    const l = latestOnly(), only = l.begin();
    l.cancel();
    expect(only()).toBe(false);
  });
});
