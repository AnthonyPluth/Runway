import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach } from "vitest";
import { clearCache } from "../lib/swr";

// What lists remember for instant paint (lib/swr.ts) lasts a visit to Runway, not a test.
beforeEach(() => clearCache());

if (typeof document !== "undefined") {
  globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} };
  Element.prototype.scrollIntoView ??= () => {};
  window.matchMedia ??= ((q: string) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, onchange: null, dispatchEvent: () => false })) as typeof window.matchMedia;
}

if (typeof document !== "undefined") {
  afterEach(() => { document.body.style.pointerEvents = ""; });
}
