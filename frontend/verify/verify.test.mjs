import { afterEach, describe, expect, it } from "vitest";
import { captureOptions, capturing, DEVICE_CACHE, findChromium, flowProblems, frozenTime, HIDE_TOASTS, holdLayout, DEFAULT_VIEWPORTS, PAGE_STYLE, PAGES, screenshotFile, selectItems,
  STILL, timeScale, TOP, unknownPages, unsettled, VIEWPORTS } from "./verify.mjs";

describe("flowProblems", () => {
  const ok = { name: "emoji-picker", page: "setup", steps: [{ goto: "#setup/categories" }, { click: "text=Emoji", timeout: 500 }] };

  it("accepts a well formed flow", () => expect(flowProblems(ok)).toEqual([]));

  it("accepts scroll_to, which takes a selector", () => {
    expect(flowProblems({ ...ok, steps: [{ scroll_to: "[data-x]" }] })).toEqual([]);
    expect(flowProblems({ ...ok, steps: [{ scroll_to: 3 }] })).toEqual(["step 1: scroll_to takes a string"]);
  });

  it("accepts expect_above, which takes a selector and the one it must end above", () => {
    expect(flowProblems({ ...ok, steps: [{ expect_above: { selector: "[data-tx]", above: "nav" } }] })).toEqual([]);
    expect(flowProblems({ ...ok, steps: [{ expect_above: "[data-tx]" }] })).toEqual(["step 1: expect_above takes a object"]);
  });

  it("names what is wrong, step by step", () => {
    expect(flowProblems({ steps: [] })).toEqual(["needs a name of letters, digits, - or _", "needs a list of steps"]);
    const bad = flowProblems({ ...ok, steps: [{ click: "a", goto: "#b" }, { fill: "text" }, { hover: "x" }] });
    expect(bad).toHaveLength(3);
    expect(bad[0]).toMatch(/step 1 must have exactly one of/);
    expect(bad[1]).toBe("step 2: fill takes a object");
    expect(bad[2]).toMatch(/step 3 must have exactly one of/);
  });

  it("accepts a scroll_to step, which takes a selector", () => {
    expect(flowProblems({ ...ok, steps: [{ scroll_to: "#inv-holdings" }] })).toEqual([]);
    expect(flowProblems({ ...ok, steps: [{ scroll_to: 3 }] })).toEqual(["step 1: scroll_to takes a string"]);
  });

  it("accepts a reload, and a signed-in flow's authenticator steps (only there, and only the two states)", () => {
    expect(flowProblems({ ...ok, steps: [{ reload: true }] })).toEqual([]);
    expect(flowProblems({ ...ok, signed_in: true, steps: [{ authenticator: "unverified" }, { authenticator: "verified" }] })).toEqual([]);
    expect(flowProblems({ ...ok, steps: [{ authenticator: "verified" }] })).toEqual(['step 1: authenticator needs "signed_in": true']);
    expect(flowProblems({ ...ok, signed_in: true, steps: [{ authenticator: "maybe" }] })).toEqual(["step 1: authenticator is verified or unverified"]);
    expect(flowProblems({ ...ok, signed_in: "yes" })).toEqual(["signed_in is true or false"]);
  });

  it("accepts the keyboard steps: tab, expect_focus and expect_inert", () => {
    expect(flowProblems({ ...ok, steps: [{ tab: { count: 3, shift: true, stay_in: "[role=dialog]" } }, { expect_focus: "button" }, { expect_inert: "nav" }] })).toEqual([]);
    expect(flowProblems({ ...ok, steps: [{ tab: 3 }] })).toEqual(["step 1: tab takes a object"]);
  });

  it("rejects a viewport it doesn't know", () => expect(flowProblems({ ...ok, viewports: ["watch"] })).toEqual(["unknown viewport watch"]));

  it("rejects a page the app doesn't have, so a typo can't pass for a clean run", () => {
    expect(flowProblems({ ...ok, page: "setings" })).toEqual(["unknown page setings"]);
    expect(unknownPages(["budget", "setings"])).toEqual(["setings"]);
    expect(unknownPages(PAGES)).toEqual([]);
  });

  it("includes the setup page", () => expect(PAGES).toContain("setup"));
});

describe("captureOptions", () => {
  it("takes the full page, but only the window for a page's first screen (top) or with a modal open", () => {
    expect(captureOptions({ timeout: 5 })).toMatchObject({ fullPage: true, timeout: 5 });
    expect(captureOptions({ top: true }).fullPage).toBe(false);
    expect(captureOptions({ modal: true }).fullPage).toBe(false);
    expect(captureOptions().style).toBe(`${HIDE_TOASTS} ${STILL}`);
    expect(screenshotFile(`budget-${TOP}`, "phone")).toBe("budget-top-phone.png");
  });
});

describe("screenshotFile", () => {
  it("names the full-page file after the page and the width", () => {
    expect(screenshotFile("budget", "phone")).toBe("budget-phone.png");
    expect(screenshotFile("flow-emoji-picker-open", "desktop")).toBe("flow-emoji-picker-open-desktop.png");
  });
});

describe("findChromium", () => {
  const tree = (files) => ({ exists: (p) => files.some((f) => f === p || f.startsWith(`${p}/`)), list: () => ["ffmpeg-1011", "chromium-1194", "chromium-1200"] });

  it("uses the newest preinstalled Chromium, wherever its folder is", () => {
    const { exists, list } = tree(["/opt/pw-browsers/chromium-1194/chrome-linux/chrome"]);
    expect(findChromium({}, exists, list)).toBe("/opt/pw-browsers/chromium-1194/chrome-linux/chrome");
    const both = tree(["/opt/pw-browsers/chromium-1194/chrome-linux/chrome", "/opt/pw-browsers/chromium-1200/chrome-linux64/chrome"]);
    expect(findChromium({}, both.exists, both.list)).toBe("/opt/pw-browsers/chromium-1200/chrome-linux64/chrome");
  });

  it("follows PLAYWRIGHT_BROWSERS_PATH, and leaves it to Playwright when there is no Chromium", () => {
    const { exists, list } = tree(["/x/chromium-1194/chrome-linux/chrome"]);
    expect(findChromium({ PLAYWRIGHT_BROWSERS_PATH: "/x" }, exists, list)).toBe("/x/chromium-1194/chrome-linux/chrome");
    expect(findChromium({ PLAYWRIGHT_BROWSERS_PATH: "/nowhere" }, exists, list)).toBeUndefined();
  });
});

describe("viewports", () => {
  it("are the iPhone 17 Pro Max and a desktop, with no tablet", () => {
    expect(VIEWPORTS).toEqual({ phone: { width: 440, height: 956 }, desktop: { width: 1280, height: 800 } });
    expect(DEFAULT_VIEWPORTS).toEqual(["phone", "desktop"]);
    expect(flowProblems({ name: "x", steps: [{ reload: true }], viewports: ["tablet"] })).toEqual(["unknown viewport tablet"]);
  });
});

describe("the frozen clock", () => {
  it("starts the browser at noon local time on the day verify.py froze the server at", () => {
    const t = frozenTime("2026-06-17");
    expect([t.getFullYear(), t.getMonth() + 1, t.getDate(), t.getHours(), t.getMinutes()]).toEqual([2026, 6, 17, 12, 0]);
  });

  it("leaves toasts out of the images, as they depend on timing", () => expect(HIDE_TOASTS).toContain("[data-sonner-toaster]"));

  it("ends transitions and dialogs' animations at once, without taking the animations away (a dialog waits for their end)", () => {
    expect(PAGE_STYLE).toContain("transition-duration: 0s !important");
    expect(PAGE_STYLE).toMatch(/\[data-state=open\][^{]*\{ animation-duration: 0s !important/);
    expect(PAGE_STYLE).not.toMatch(/\[data-state=closed\]|\[data-state\][^=]/);   // closing keeps its animation (and the scroll it restores)
    expect(PAGE_STYLE).not.toMatch(/animation: none/);
  });
});

describe("selectItems (PAGES=…)", () => {
  const flows = [{ name: "budget-edit", page: "budget", steps: [] }, { name: "emoji", page: "setup", steps: [] }, { name: "home", steps: [] }];
  const ids = (r) => r.items.map((i) => i.id);

  it("runs everything when no names are given, the device cache check last", () => {
    expect(ids(selectItems([], flows))).toEqual([...PAGES.map((p) => `page:${p}`), "flow:budget-edit", "flow:emoji", "flow:home", DEVICE_CACHE]);
  });

  it("limits a run to a page and the flows that start on it", () => {
    expect(ids(selectItems(["budget"], flows))).toEqual(["page:budget", "flow:budget-edit"]);
    expect(ids(selectItems(["overview"], flows))).toEqual(["page:overview", "flow:home"]);   // a flow's page defaults to overview
  });

  it("takes a flow by its name alone, without its page", () => {
    expect(ids(selectItems(["emoji"], flows))).toEqual(["flow:emoji"]);
    expect(ids(selectItems([DEVICE_CACHE], flows))).toEqual([DEVICE_CACHE]);
    expect(ids(selectItems(["transactions"], flows))).toEqual(["page:transactions", DEVICE_CACHE]);
  });

  it("names what it doesn't know rather than running nothing (or everything)", () => {
    expect(selectItems(["budegt", "emoji"], flows)).toEqual({ unknown: ["budegt"] });
  });

  it("narrows to the items a retry asks for", () => {
    expect(ids(selectItems([], flows, ["flow:emoji", "page:setup"]))).toEqual(["page:setup", "flow:emoji"]);
  });

  it("keeps flow names apart from pages, so a name picks one thing", () => {
    expect(flowProblems({ name: "budget", steps: [{ reload: true }] })).toEqual(["the name budget is taken (a page, or the device cache check)"]);
  });
});

describe("timeScale", () => {
  it("stretches the waits with the load per CPU, at most 8 times, and more on a retry", () => {
    expect(timeScale(1, 1, 0.5, 4)).toBe(1);
    expect(timeScale(1, 1, 12, 4)).toBe(3);
    expect(timeScale(1, 1, 77, 4)).toBe(8);
    expect(timeScale(1, 2, 12, 4)).toBe(6);
    expect(timeScale(5, 1, 0, 4)).toBe(5);   // what runway/verify.py asked for, when the load has dropped since
  });
});

describe("unsettled", () => {
  // A page as unsettled() reads it: elements with a size, classes and attributes, in a stand-in document.
  const el = (o = {}) => ({ tagName: "DIV", className: o.cls ?? "", getAttribute: (a) => o.attrs?.[a] ?? null, textContent: o.text ?? "",
    getClientRects: () => (o.hidden ? [] : [{}]), getBoundingClientRect: () => ({ width: o.w ?? 200, height: o.h ?? 40 }) });
  const page = ({ pulse = [], busy = [], badge = null, animations = [], readyState = "complete", fonts = "loaded" } = {}) => {
    globalThis.getComputedStyle = () => ({ visibility: "visible" });
    globalThis.document = { readyState, fonts: { status: fonts }, getAnimations: () => animations,
      querySelectorAll: (s) => (s === ".animate-pulse" ? pulse : s === "[aria-busy=true]" ? busy : []),
      querySelector: (s) => (s === "[data-testid=holdings-updated]" ? badge : null) };
    return unsettled();
  };
  afterEach(() => { delete globalThis.document; delete globalThis.getComputedStyle; });

  it("finds nothing on a loaded page", () => expect(page()).toEqual([]));

  it("finds a loading skeleton, but not a pulsing status dot or a hidden placeholder", () => {
    expect(page({ pulse: [el({ cls: "h-40 animate-pulse rounded-xl bg-muted" })] })[0]).toMatch(/a loading placeholder is showing \(div\.h-40\.animate-pulse/);
    expect(page({ pulse: [el({ cls: "size-1.5 rounded-full animate-pulse", w: 6, h: 6 })] })).toEqual([]);
    expect(page({ pulse: [el({ cls: "h-40 animate-pulse", hidden: true })] })).toEqual([]);
  });

  it("finds a list being refreshed (aria-busy)", () => {
    expect(page({ busy: [el({ attrs: { "aria-label": "Loading" } })] })).toEqual(['still busy (aria-busy on div[aria-label="Loading"])']);
  });

  it("waits for the Investments page's live prices, not the holdings' last sync", () => {
    expect(page({ badge: el({ text: " Holdings updated never " }) })).toEqual(["the live prices haven't arrived (\"Holdings updated never\")"]);
    expect(page({ badge: el({ text: "Market closed · latest prices as of 12:00:00 PM" }) })).toEqual([]);
  });

  it("waits for a transition to end, not for an endless animation", () => {
    const anim = (endTime) => ({ playState: "running", transitionProperty: "transform", effect: { getComputedTiming: () => ({ endTime }) } });
    expect(page({ animations: [anim(150)] })).toEqual(["1 animation is still running (transform)"]);
    expect(page({ animations: [anim(Infinity)] })).toEqual([]);
  });

  it("doesn't wait for what the images leave out (a toast, the \"Saved ✓\" that fades after each save)", () => {
    const fading = { playState: "running", animationName: "saved-fade", effect: { getComputedTiming: () => ({ endTime: 1600 }),
      target: { closest: (s) => (s.includes("[style*=saved-fade]") ? {} : null) } } };
    expect(page({ animations: [fading] })).toEqual([]);
    for (const s of unsettled.toString().match(/hidden = "([^"]+)"/)[1].split(", ")) expect(HIDE_TOASTS).toContain(s);
  });

  it("waits for the document and its fonts", () => {
    expect(page({ readyState: "interactive", fonts: "loading" })).toEqual(["the document is still loading", "fonts are still loading"]);
  });
});

describe("holdLayout", () => {
  it("keeps media query changes and resizes from the app while a screenshot is taken", async () => {
    const queryListeners = [], windowListeners = [];
    class MediaQueryList { addEventListener(_type, fn) { queryListeners.push(fn); } removeEventListener() {} }
    const saved = { addEventListener: globalThis.addEventListener, dispatchEvent: globalThis.dispatchEvent };
    globalThis.MediaQueryList = MediaQueryList;
    globalThis.addEventListener = (type, fn) => windowListeners.push([type, fn]);
    globalThis.dispatchEvent = () => true;
    globalThis.requestAnimationFrame = (f) => setTimeout(f, 0);
    try {
      holdLayout();
      const heard = [];
      new MediaQueryList().addEventListener("change", (e) => heard.push(e.matches));
      queryListeners[0]({ matches: true });
      await capturing(true);
      queryListeners[0]({ matches: false });   // the capture's 1×1 window: the phone query flips for a moment
      let stopped = false;
      windowListeners.find(([t]) => t === "resize")[1]({ stopImmediatePropagation: () => { stopped = true; } });
      await capturing(false);
      queryListeners[0]({ matches: true });
      expect(stopped).toBe(true);
      expect(heard).toEqual([true, true]);   // the change during the capture never reached the app
    } finally {
      delete globalThis.MediaQueryList;
      delete globalThis.requestAnimationFrame;
      delete globalThis.__verifyCapturing;
      Object.assign(globalThis, saved);
    }
  });
});
