// @vitest-environment jsdom
/* global document, Element -- the jsdom environment above */
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { applyBaseline, axeProblems, BASELINE, focusProblems, inheritedWhere, inheritProblems, MIN_TAP, parseBaseline, readBaseline, shadowRinged, canonical, strokeRinged, tapBoxes, tapProblems, ringedBy, visitProblems } from "./a11y.mjs";

describe("axeProblems", () => {
  it("names each failing element with its rule", () => {
    const violations = [{ id: "color-contrast", help: "Elements must meet contrast", nodes: [{ target: ["p.muted"] }, { target: ["iframe", "span"] }] }];
    expect(axeProblems(violations)).toEqual(["color-contrast: p.muted (Elements must meet contrast)", "color-contrast: iframe span (Elements must meet contrast)"]);
    expect(axeProblems([])).toEqual([]);
  });
});

describe("tapProblems", () => {
  const boxes = [{ target: "button A", width: MIN_TAP, height: MIN_TAP }, { target: "button B", width: 32, height: 44 }, { target: "a C", width: 100, height: 20.4 }];
  it("flags targets under 44 px on the phone only", () => {
    expect(tapProblems(boxes, "phone")).toEqual([`tap-target: button B is 32×44 px, under ${MIN_TAP}`, `tap-target: a C is 100×20 px, under ${MIN_TAP}`]);
    expect(tapProblems(boxes, "desktop")).toEqual([]);
    expect(tapProblems(boxes, "landscape")).toEqual([]);
  });
});

describe("focusProblems", () => {
  it("flags a stop with no ring", () => {
    expect(focusProblems([{ target: "a X", ringed: true }, { target: "g Y", ringed: false }])).toEqual(["focus: g Y shows no focus ring"]);
  });
});

describe("shadowRinged", () => {
  it("is false for no shadow and for Tailwind's empty rings", () => {
    expect(shadowRinged("none")).toBe(false);
    expect(shadowRinged("")).toBe(false);
    expect(shadowRinged("rgba(0, 0, 0, 0) 0px 0px 0px 0px, rgba(0, 0, 0, 0) 0px 0px 0px 0px, rgba(0, 0, 0, 0) 0px 0px 0px 0px")).toBe(false);
    expect(shadowRinged("rgb(255, 255, 255) 0px 0px 0px 0px")).toBe(false);
  });
  it("is true for a visible ring, whatever the color function", () => {
    expect(shadowRinged("rgba(0, 0, 0, 0) 0px 0px 0px 0px, rgb(59, 130, 246) 0px 0px 0px 3px")).toBe(true);
    expect(shadowRinged("oklab(0.62 -0.03 -0.19 / 0.5) 0px 0px 0px 2px")).toBe(true);
    expect(shadowRinged("0px 0px 0px 2px color(srgb 0.2 0.4 0.9)")).toBe(true);
  });
  it("is false for a transparent ring in any notation", () => {
    expect(shadowRinged("oklab(0.62 -0.03 -0.19 / 0) 0px 0px 0px 2px")).toBe(false);
    expect(shadowRinged("rgba(10, 20, 30, 0) 0px 0px 0px 4px")).toBe(false);
  });
});

describe("strokeRinged", () => {
  it("is a drawn stroke of 2 px or more", () => {
    expect(strokeRinged("rgb(255, 255, 255)", "2px")).toBe(true);
    expect(strokeRinged("rgb(255, 255, 255)", "3.5px")).toBe(true);
  });
  it("is not a thin, absent or transparent one", () => {
    expect(strokeRinged("rgb(255, 255, 255)", "1px")).toBe(false);
    expect(strokeRinged("none", "2px")).toBe(false);
    expect(strokeRinged("", "2px")).toBe(false);
    expect(strokeRinged("rgba(0, 0, 0, 0)", "2px")).toBe(false);
    expect(strokeRinged("oklab(0.5 0 0 / 0)", "2px")).toBe(false);
  });
});

describe("the baseline file", () => {
  const good = "overview @ phone | tap-target: button A is 32×44 px, under 44 | a small icon button, fixed in #123";
  it("parses a line into its page or flow, finding and reason; # lines and blanks are comments", () => {
    const { entries, errors } = parseBaseline(`# header\n\n${good}\n`);
    expect(errors).toEqual([]);
    expect(entries).toEqual([{ where: "overview @ phone", problem: "tap-target: button A is 32×44 px, under 44", reason: "a small icon button, fixed in #123", key: "overview @ phone | tap-target: button A is 32×44 px, under 44" }]);
  });
  it("keeps a ' | ' inside the finding", () => {
    const { entries } = parseBaseline("flow x @ desktop | color-contrast: a | b (help) | why");
    expect(entries[0].problem).toBe("color-contrast: a | b (help)");
    expect(entries[0].reason).toBe("why");
  });
  it("rejects a line with no reason, an unknown width or a repeat", () => {
    expect(parseBaseline("overview @ phone | tap-target: x").errors).toHaveLength(1);
    expect(parseBaseline("overview @ phone | tap-target: x |  ").errors).toHaveLength(1);
    expect(parseBaseline("overview @ tablet | tap-target: x | why").errors[0]).toMatch(/doesn't end in @ phone/);
    expect(parseBaseline(`${good}\n${good}`).errors).toEqual(["a11y-baseline.txt line 2: listed twice"]);
  });
  it("reads a missing file as an empty baseline", () => {
    const dir = mkdtempSync(join(tmpdir(), "a11y-"));
    try {
      expect(readBaseline(join(dir, "none.txt"))).toEqual({ entries: [], errors: [] });
      writeFileSync(join(dir, "b.txt"), good);
      expect(readBaseline(join(dir, "b.txt")).entries).toHaveLength(1);
    } finally { rmSync(dir, { recursive: true, force: true }); }
  });
  it("is well formed in the repository, and every line gives a reason", () => {
    const { entries, errors } = readBaseline(BASELINE);
    expect(errors).toEqual([]);
    for (const e of entries) expect(e.reason.length).toBeGreaterThan(10);
  });
});

describe("applyBaseline and visitProblems", () => {
  const where = "budget @ phone";
  const entries = parseBaseline([`${where} | tap-target: button A is 32×44 px, under 44 | known`, `${where} | focus: a B shows no focus ring | known`, "setup @ phone | focus: a C shows no focus ring | other page"].join("\n")).entries;
  it("tolerates a baselined finding", () => {
    expect(applyBaseline(["tap-target: button A is 32×44 px, under 44", "focus: a B shows no focus ring"], where, entries)).toEqual({ fresh: [], tolerated: 2, stale: [] });
    expect(visitProblems(["tap-target: button A is 32×44 px, under 44", "focus: a B shows no focus ring"], where, entries)).toEqual([]);
  });
  it("fails a new finding, and a finding baselined for another page or width", () => {
    expect(visitProblems(["focus: a C shows no focus ring", "tap-target: button A is 30×44 px, under 44"], where, entries)).toEqual([
      `${where}: a11y focus: a C shows no focus ring`,
      `${where}: a11y tap-target: button A is 30×44 px, under 44`,
      `${where}: a11y-baseline.txt lists "tap-target: button A is 32×44 px, under 44", which no longer occurs: delete the line`,
      `${where}: a11y-baseline.txt lists "focus: a B shows no focus ring", which no longer occurs: delete the line`,
    ]);
    expect(visitProblems(["tap-target: button A is 32×44 px, under 44", "focus: a B shows no focus ring"], "budget @ desktop", entries)).toEqual([
      "budget @ desktop: a11y tap-target: button A is 32×44 px, under 44",
      "budget @ desktop: a11y focus: a B shows no focus ring",
    ]);
  });
  it("fails a baseline line whose finding is gone, so the list only shrinks", () => {
    expect(applyBaseline(["focus: a B shows no focus ring"], where, entries).stale.map((e) => e.problem)).toEqual(["tap-target: button A is 32×44 px, under 44"]);
    expect(visitProblems([], where, entries)).toHaveLength(2);
  });
  it("leaves a page the run didn't visit alone", () => {
    expect(visitProblems([], "networth @ phone", entries)).toEqual([]);
  });
  it("ignores Svelte's scoped class names, which change with the component's CSS", () => {
    expect(canonical("color-contrast: .svelte-1a2b3c > button.svelte-zz9 (x)")).toBe("color-contrast:  > button (x)");
    const e = parseBaseline(`${where} | color-contrast: button (x) | known`).entries;
    expect(visitProblems(["color-contrast: button.svelte-q1w2e3 (x)"], where, e)).toEqual([]);
  });
  it("ignores generated ids and a chart shape's coordinates", () => {
    expect(canonical("target-size: button#bits-c114.x (h)")).toBe("target-size: button#bits-c*.x (h)");
    expect(canonical("tap-target: input#c22.border-transparent \"Inflation\" is 156×36 px, under 44")).toBe("tap-target: input#c*.border-transparent");
    expect(canonical("tap-target: input#c10-inflation.x \"A\" is 1×1 px, under 44")).toBe("tap-target: input#c*-inflation.x");
    expect(canonical("label-content-name-mismatch: rect[y=\"240.4038\"] (h)")).toBe("label-content-name-mismatch: rect[y] (h)");
    expect(canonical("target-size: [aria-controls=\"c12-card\"] (h)")).toBe("target-size: [aria-controls=\"c*-card\"] (h)");
    expect(canonical("focus: input#name-demo-checking \"A\" shows no focus ring")).toBe("focus: input#name-demo-checking \"A\" shows no focus ring");
  });
  it("names a tap target by its tag, id and classes: not its label or its size", () => {
    expect(canonical("tap-target: button.block.cursor-pointer \"Logo for Corner Market\" is 36×36 px, under 44")).toBe("tap-target: button.block.cursor-pointer");
    expect(canonical("tap-target: button.block.cursor-pointer \"Logo for \\\"X\\\"\" is 20×36 px, under 44")).toBe("tap-target: button.block.cursor-pointer");
    expect(canonical("tap-target: button.x is 20×36 px, under 44")).toBe("tap-target: button.x");
    const e = parseBaseline(`${where} | tap-target: button.block.cursor-pointer | rows`).entries;
    const tall = "tap-target: button.block.cursor-pointer \"Logo for Target\" is 36×40 px, under 44";
    expect(visitProblems([tall, "tap-target: button.block.cursor-pointer \"Logo for Fuel Stop\" is 36×36 px, under 44"], where, e)).toEqual([]);
    expect(visitProblems(["tap-target: button.other \"Logo\" is 36×36 px, under 44"], where, e)).toHaveLength(2);
  });
});

describe("ringedBy", () => {
  const plain = { outlineStyle: "none", outlineWidth: "0px", outlineColor: "rgb(0, 0, 0)", boxShadow: "none", stroke: "none", strokeWidth: "1px", svg: false };
  const SHADOW = "rgba(0, 0, 0, 0.05) 0px 1px 2px 0px";
  it("is a ring when focus adds an outline, a box shadow or a stroke", () => {
    expect(ringedBy({ ...plain, outlineStyle: "solid", outlineWidth: "2px", outlineColor: "rgb(59, 130, 246)" }, plain)).toBe(true);
    expect(ringedBy({ ...plain, boxShadow: "rgb(59, 130, 246) 0px 0px 0px 3px" }, plain)).toBe(true);
    expect(ringedBy({ ...plain, boxShadow: `rgb(59, 130, 246) 0px 0px 0px 3px, ${SHADOW}` }, { ...plain, boxShadow: SHADOW })).toBe(true);
    expect(ringedBy({ ...plain, svg: true, stroke: "rgb(255, 255, 255)", strokeWidth: "2px" }, { ...plain, svg: true, stroke: "rgb(0, 0, 0)", strokeWidth: "1px" })).toBe(true);
    expect(ringedBy({ ...plain, svg: true, stroke: "rgb(255, 255, 255)", strokeWidth: "1px" }, { ...plain, svg: true })).toBe(false);
  });
  it("is not a ring when the same shadow, outline or stroke is there unfocused (a button's shadow-xs)", () => {
    expect(ringedBy({ ...plain, boxShadow: SHADOW }, { ...plain, boxShadow: SHADOW })).toBe(false);
    const outlined = { ...plain, outlineStyle: "solid", outlineWidth: "1px", outlineColor: "rgb(1, 2, 3)" };
    expect(ringedBy(outlined, outlined)).toBe(false);
    expect(ringedBy(plain, plain)).toBe(false);
  });
});

describe("tapBoxes", () => {
  it("measures an interactive SVG shape but not a decorative one", async () => {
    document.body.innerHTML = `<svg><rect role="button" tabindex="0" aria-label="Bar" width="30" height="10"/><rect width="30" height="10"/><g aria-hidden="true"><path role="button" tabindex="0" width="20" height="20"/></g></svg>`;
    // jsdom lays nothing out: a shape is as big as its width and height attributes say.
    const rect = vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function () {
      return { width: Number(this.getAttribute("width") ?? 0), height: Number(this.getAttribute("height") ?? 0) };
    });
    try {
      // The page's evaluate runs its callback right here, in the jsdom document.
      const boxes = await tapBoxes({ evaluate: async (fn) => fn() });
      expect(boxes.map((b) => b.target)).toEqual(['rect "Bar"']);
      expect(tapProblems(boxes, "phone")).toHaveLength(1);
    } finally { rect.mockRestore(); }
  });
});

describe("a flow that inherits its page's baseline", () => {
  const page = "setup @ phone";
  const flow = "flow account-delete @ phone";
  const known = "tap-target: button.block is 40×44 px, under 44";
  const own = "tap-target: a.dialog-link is 40×44 px, under 44";
  // A baseline line holds the canonical form of a finding (a tap target without its size).
  const entries = parseBaseline([`${page} | ${canonical(known)} | page`, `${page} | focus: a P shows no focus ring | page`, `${flow} | ${canonical(own)} | flow`].join("\n")).entries;
  it("tolerates the page's finding at the same width", () => {
    expect(applyBaseline([known], flow, entries, page)).toMatchObject({ fresh: [], tolerated: 1 });
    expect(visitProblems([known, own], flow, entries, page)).toEqual([]);
  });
  it("fails a finding only the flow has (the dialog's own)", () => {
    expect(visitProblems([known, "color-contrast: [role=dialog] p (Elements must meet minimum color contrast ratio thresholds)"], flow, entries, page)).toEqual([
      `${flow}: a11y color-contrast: [role=dialog] p (Elements must meet minimum color contrast ratio thresholds)`,
      `${flow}: a11y-baseline.txt lists "${canonical(own)}", which no longer occurs: delete the line`,
    ]);
  });
  it("fails a finding baselined for the page at another width", () => {
    expect(visitProblems([known], "flow account-delete @ desktop", entries, "setup @ desktop")).toEqual([`flow account-delete @ desktop: a11y ${known}`]);
  });
  it("does not inherit without being asked", () => {
    expect(visitProblems([known], flow, entries).slice(0, 1)).toEqual([`${flow}: a11y ${known}`]);
  });
  it("does not report the page's lines stale: its own visit checks them", () => {
    expect(visitProblems([own], flow, entries, page)).toEqual([]);
  });
  it("still flags a page line whose finding is gone, in the page's own visit", () => {
    expect(visitProblems([], page, entries)).toEqual([
      `${page}: a11y-baseline.txt lists "${canonical(known)}", which no longer occurs: delete the line`,
      `${page}: a11y-baseline.txt lists "focus: a P shows no focus ring", which no longer occurs: delete the line`,
    ]);
  });
  const PAGES = ["budget", "setup"];
  const WIDTHS = ["phone", "desktop"];
  it("takes its page from the flow's own page field, written out", () => {
    expect(inheritedWhere({ name: "f", page: "setup", inherits_page_baseline: true }, "phone", PAGES, WIDTHS)).toBe("setup @ phone");
    expect(inheritedWhere({ name: "f", page: "setup" }, "phone", PAGES, WIDTHS)).toBeUndefined();
  });
  it("fails loudly when the page can't be determined", () => {
    expect(inheritProblems({ name: "f", inherits_page_baseline: true }, PAGES, WIDTHS)).toHaveLength(1);
    expect(inheritProblems({ name: "f", page: "nope", inherits_page_baseline: true }, PAGES, WIDTHS)).toEqual(["inherits_page_baseline: unknown page nope"]);
    expect(inheritProblems({ name: "f", page: "setup", inherits_page_baseline: true, viewports: ["landscape"] }, PAGES, WIDTHS)).toHaveLength(1);
    expect(inheritProblems({ name: "f", page: "setup", inherits_page_baseline: "yes" }, PAGES, WIDTHS)).toHaveLength(1);
    expect(() => inheritedWhere({ name: "f", inherits_page_baseline: true }, "phone", PAGES, WIDTHS)).toThrow(/flow f:/);
  });
});
