// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { niceTicks, scrub, sideways, xScale, yScale } from "./chart";

describe("niceTicks", () => {
  it("steps by round numbers covering the range", () => {
    expect(niceTicks(0, 100)).toEqual([0, 20, 40, 60, 80, 100]);
    expect(niceTicks(0, 1234)).toEqual([0, 250, 500, 750, 1000, 1250]);
    expect(niceTicks(13, 87)).toEqual([0, 20, 40, 60, 80, 100]);
  });
  it("spans zero", () => {
    expect(niceTicks(-50, 50)).toEqual([-60, -40, -20, 0, 20, 40, 60]);
  });
  it("doesn't pile up floating-point dust", () => {
    expect(niceTicks(0, 0.7)).toEqual([0, 0.2, 0.4, 0.6, 0.8]);
  });
  it("copes with a flat line", () => {
    expect(niceTicks(5, 5)).toEqual([5]);
    expect(niceTicks(0, 0)).toEqual([0]);
  });
  it("takes a tick count", () => {
    expect(niceTicks(0, 100, 2)).toEqual([0, 50, 100]);
  });
});

describe("niceTicks edge cases", () => {
  it("covers a range that spans zero", () => {
    const t = niceTicks(-130, 480);
    expect(t[0]).toBeLessThanOrEqual(-130);
    expect(t.at(-1)).toBeGreaterThanOrEqual(480);
    expect(t).toContain(0);
  });
  it("rounds away floating point dust in small steps", () => {
    expect(niceTicks(0, 0.3)).toEqual([0, 0.1, 0.2, 0.3, 0.4].slice(0, niceTicks(0, 0.3).length));
  });
  it("gives a single tick, not NaN or a hang, when everything is one value", () => {
    expect(niceTicks(50, 50)).toEqual([50]);
  });
});

describe("scales", () => {
  it("puts the top tick at the top and the bottom one a height below", () => {
    const { y0, y1, y } = yScale([0, 50, 100], 10, 200);
    expect([y0, y1, y(100), y(0), y(50)]).toEqual([0, 100, 10, 210, 110]);
  });
  it("copes with a single tick", () => {
    expect(yScale([5], 0, 100).y(5)).toBe(100);
  });
  it("spreads points evenly from left across width", () => {
    const x = xScale(0, 4, 50, 400);
    expect([x(0), x(2), x(4)]).toEqual([50, 250, 450]);
    expect(xScale(3, 3, 50, 400)(3)).toBe(50);
    expect(xScale(2, 4, 0, 100)(3)).toBe(50);
  });
});

describe("scrub and sideways", () => {
  const touch = (x: number, y: number) => ({ clientX: x, clientY: y });
  const fire = (el: Element, type: string, t: ReturnType<typeof touch>) => {
    const e = new Event(type, { cancelable: true }) as Event & { touches: unknown[] };
    e.touches = [t];
    el.dispatchEvent(e);
    return e;
  };

  it("follows a sideways drag and keeps the page from scrolling with it", () => {
    const el = document.createElement("div"), moves: number[] = [];
    scrub(el, (x) => moves.push(x));
    fire(el, "touchstart", touch(10, 10));
    const e = fire(el, "touchmove", touch(30, 12));
    expect(moves).toEqual([10, 30]);
    expect(e.defaultPrevented).toBe(true);
  });

  it("lets a mostly vertical drag scroll the page", () => {
    const el = document.createElement("div"), moves: number[] = [];
    scrub(el, (x) => moves.push(x));
    fire(el, "touchstart", touch(10, 10));
    const e = fire(el, "touchmove", touch(12, 60));
    expect(moves).toEqual([10]);
    expect(e.defaultPrevented).toBe(false);
  });

  it("stops listening when destroyed", () => {
    const el = document.createElement("div"), moves: number[] = [];
    const a = scrub(el, (x) => moves.push(x));
    a.destroy();
    fire(el, "touchstart", touch(1, 1));
    expect(moves).toEqual([]);
  });

  it("sideways leaves the touch itself to the tap's mouse events, and follows a changed handler", () => {
    const el = document.createElement("div"), moves: number[] = [], later: number[] = [];
    const a = sideways(el, (x) => moves.push(x));
    fire(el, "touchstart", touch(10, 10));
    fire(el, "touchmove", touch(30, 12));
    a.update((x) => later.push(x));
    fire(el, "touchmove", touch(40, 12));
    expect(moves).toEqual([30]);
    expect(later).toEqual([40]);
  });
});
