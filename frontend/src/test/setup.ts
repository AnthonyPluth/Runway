import "@testing-library/jest-dom/vitest";
import userEvent from "@testing-library/user-event";
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

// Bits UI locks the page (`pointer-events: none` on <body>) while a sheet, dialog or menu is open and lifts it from a timer after
// it closes, so a click made right after one closes is refused ("element has pointer-events: none"), under CI load more than
// locally. A person waits for the page to answer again; so does a pointer action here: it first waits (up to a second) for the
// lock to clear, unless something modal is still open (its own controls stay clickable, and a stuck lock then still fails).
if (typeof document !== "undefined") {
  const realSetTimeout = setTimeout;   // a test's fake timers must not stall the wait
  const modalOpen = () => document.querySelector('[role="dialog"], [role="alertdialog"], [role="menu"], [role="listbox"]') !== null;
  const pageClickable = async () => {
    for (let i = 0; i < 100 && document.body.style.pointerEvents === "none" && !modalOpen(); i++) await new Promise((r) => realSetTimeout(r, 10));
  };
  const pointerActions = ["click", "dblClick", "tripleClick", "hover", "unhover", "pointer", "type", "selectOptions", "deselectOptions", "upload"] as const;
  const waiting = <T extends object>(api: T): T => {
    for (const name of pointerActions) {
      const original = (api as Record<string, unknown>)[name];
      if (typeof original !== "function") continue;
      (api as Record<string, unknown>)[name] = async (...args: unknown[]) => { await pageClickable(); return original.apply(api, args); };
    }
    return api;
  };
  waiting(userEvent);
  const setup = userEvent.setup;
  (userEvent as { setup: typeof setup }).setup = ((...args: Parameters<typeof setup>) => waiting(setup(...args))) as typeof setup;
}
