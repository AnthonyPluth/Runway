// Runs before every test file. In component tests (jsdom) it adds the DOM matchers (toBeInTheDocument, ...) and
// stubs what jsdom lacks; in Node tests it does nothing, so they stay fast.
import "@testing-library/jest-dom/vitest";

if (typeof document !== "undefined") {
  // jsdom has no layout engine: these are what bits-ui's popovers and Svelte transitions ask for.
  globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} };
  Element.prototype.scrollIntoView ??= () => {};
  window.matchMedia ??= ((q: string) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, onchange: null, dispatchEvent: () => false })) as typeof window.matchMedia;
}
