// Runs before every test file. In component tests (jsdom) it adds the DOM matchers (toBeInTheDocument, ...) and
// stubs what jsdom lacks; in Node tests it does nothing, so they stay fast.
import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";

if (typeof document !== "undefined") {
  // jsdom has no layout engine: these are what bits-ui's popovers and Svelte transitions ask for.
  globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} };
  Element.prototype.scrollIntoView ??= () => {};
  window.matchMedia ??= ((q: string) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, onchange: null, dispatchEvent: () => false })) as typeof window.matchMedia;
}

if (typeof document !== "undefined") {
  // A dialog or side panel (Bits UI) makes the rest of the page inert with pointer-events: none on <body> until it has
  // finished closing. A test that ends mid-close would leave the next test in the file unable to click anything.
  afterEach(() => { document.body.style.pointerEvents = ""; });
}
