/// <reference types="node" />
import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";

function media(initial: boolean) {
  const listeners: ((e: { matches: boolean }) => void)[] = [];
  const mq = { matches: initial, addEventListener: (_: string, fn: (e: { matches: boolean }) => void) => listeners.push(fn) };
  const matchMedia = vi.fn(() => mq);
  vi.stubGlobal("matchMedia", matchMedia);
  return { matchMedia, fire: (matches: boolean) => listeners.forEach((fn) => fn({ matches })) };
}
const load = async () => { vi.resetModules(); return import("./rail.svelte"); };

afterEach(() => { vi.unstubAllGlobals(); });

describe("tablet.rail", () => {
  it("covers 768 through 1023 px, and never a phone turned sideways", async () => {
    media(false);
    const { RAIL_QUERY } = await load();
    expect(RAIL_QUERY).toBe("(min-width: 768px) and (max-width: 1023px) and (not ((max-height: 500px) and (pointer: coarse)))");
  });

  it("starts as the query says and follows the screen", async () => {
    const m = media(true);
    const { tablet, RAIL_QUERY } = await load();
    expect(m.matchMedia).toHaveBeenCalledExactlyOnceWith(RAIL_QUERY);
    expect(tablet.rail).toBe(true);
    m.fire(false);
    expect(tablet.rail).toBe(false);
  });

  it("is not a rail where there is no matchMedia", async () => {
    vi.stubGlobal("matchMedia", undefined);
    expect((await load()).tablet.rail).toBe(false);
  });

  it("is the same query the rail: styles use", async () => {
    media(false);
    const { RAIL_QUERY } = await load();
    const css = readFileSync(new URL("../app.css", import.meta.url), "utf8");
    expect(css).toContain(`@custom-variant rail (@media ${RAIL_QUERY});`);
    expect(css).toContain("@custom-variant wide (@media (min-width: 1024px) and (not ((max-height: 500px) and (pointer: coarse))));");
  });
});
