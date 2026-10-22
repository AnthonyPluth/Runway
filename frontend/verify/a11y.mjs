/* global document, window, getComputedStyle -- used inside page.evaluate callbacks, which run in the browser */
// The accessibility checks `make verify` makes on every page and flow it visits (ported from Waypoint's verify/a11y.mjs):
// axe-core's WCAG 2.2 AA rules, a 44 px minimum for a tap target on the phone, and a visible focus ring on every tab stop.
// A finding is a line of text; baseline.txt-style tolerance of known ones (a list that only shrinks) is applied by
// applyBaseline, so a NEW finding fails and a baselined one that no longer occurs fails too.
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export const AXE_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];
// Rules that judge a whole document, not Runway's screens: a hash-routed single page app has one <main> and no landmark
// regions of its own per route, and a sheet or a scroll area is reached by pointer or has its own keyboard handling.
export const AXE_OFF = ["region", "landmark-one-main", "page-has-heading-one", "landmark-unique", "scrollable-region-focusable"];
export const MIN_TAP = 44;
export const MAX_TABS = 80;
export const BASELINE = join(dirname(fileURLToPath(import.meta.url)), "a11y-baseline.txt");

let axeSource;
const axe = () => (axeSource ??= readFileSync(createRequire(import.meta.url).resolve("axe-core/axe.min.js"), "utf8"));

export const axeProblems = (violations) => violations.flatMap((v) => v.nodes.map((n) => `${v.id}: ${n.target.join(" ")} (${v.help})`));

export function tapProblems(boxes, viewport) {
  if (viewport !== "phone") return [];
  return boxes.filter((b) => b.width < MIN_TAP || b.height < MIN_TAP).map((b) => `tap-target: ${b.target} is ${Math.round(b.width)}×${Math.round(b.height)} px, under ${MIN_TAP}`);
}

export function focusProblems(stops) {
  return stops.filter((s) => !s.ringed).map((s) => `focus: ${s.target} shows no focus ring`);
}

/** Whether a computed `box-shadow` draws something: a shadow with some size (a blur, spread or offset) in a color that is not
 *  fully transparent. Tailwind's rings are always there as `rgba(0, 0, 0, 0) 0px 0px 0px 0px`, which draws nothing.
 *  Self-contained: it is also sent to the page. */
export function shadowRinged(boxShadow) {
  if (!boxShadow || boxShadow === "none") return false;
  const parts = [];
  for (let depth = 0, start = 0, i = 0; i <= boxShadow.length; i++) {
    const c = boxShadow[i];
    if (c === "(") depth++;
    else if (c === ")") depth--;
    else if ((c === "," && depth === 0) || i === boxShadow.length) { parts.push(boxShadow.slice(start, i)); start = i + 1; }
  }
  return parts.some((part) => {
    const color = part.match(/(?:rgba?|hsla?|oklab|oklch|lab|lch|color)\([^)]*\)/)?.[0] ?? "";
    const lengths = (part.replace(color, "").match(/-?\d*\.?\d+px/g) ?? []).map(parseFloat);
    const alpha = color.match(/(?:,|\/)\s*([\d.]+%?)\s*\)$/)?.[1];
    const clear = alpha !== undefined && parseFloat(alpha) === 0;
    return !clear && lengths.some((n) => n !== 0);
  });
}

/** Whether an SVG shape's computed stroke is a ring: drawn, and 2 px or more (the app's own shapes are 1 px outlines until
 *  focused, as the Reports flow chart's). Self-contained: it is also sent to the page. */
export function strokeRinged(stroke, width) {
  return !!stroke && stroke !== "none" && !/(,|\/)\s*0(\.0+)?\s*\)$/.test(stroke) && parseFloat(width) >= 2;
}

export async function runAxe(page) {
  await page.evaluate(axe());
  const result = await page.evaluate(async ({ tags, off }) => globalThis.axe.run(document, { runOnly: { type: "tag", values: tags }, rules: Object.fromEntries(off.map((id) => [id, { enabled: false }])) }), { tags: AXE_TAGS, off: AXE_OFF });
  return axeProblems(result.violations);
}

export async function tapBoxes(page) {
  return page.evaluate(() => {
    const name = (el) => `${el.tagName.toLowerCase()}${el.id ? `#${el.id}` : ""}${[...el.classList].slice(0, 2).map((c) => `.${c}`).join("")} "${(el.getAttribute("aria-label") || el.textContent || "").trim().slice(0, 24)}"`;
    const inline = (el) => el.tagName === "A" && getComputedStyle(el).display === "inline" && !!el.closest("p, li, span, td");
    const box = (el) => (el.matches("input[type=checkbox], input[type=radio]") ? el.closest("label") ?? el : el);
    return [...document.querySelectorAll("a[href], button, input:not([type=hidden]), select, textarea, [role=button], [role=tab], [role=link], [tabindex]:not([tabindex='-1'])")]
      .filter((el) => { const s = getComputedStyle(el); const r = el.getBoundingClientRect(); return s.visibility !== "hidden" && s.display !== "none" && !el.disabled && r.width > 0 && r.height > 0 && !el.closest(".sr-only, [aria-hidden=true], svg") && !inline(el) && !(el.classList.contains("sr-only")); })
      .map((el) => { const r = box(el).getBoundingClientRect(); return { target: name(el), width: r.width, height: r.height }; });
  });
}

export async function focusStops(page) {
  await page.evaluate(() => { document.activeElement?.blur?.(); window.scrollTo(0, 0); });
  const probe = `(() => {
    const shadowRinged = ${shadowRinged};
    const strokeRinged = ${strokeRinged};
    const el = document.activeElement;
    if (!el || el === document.body || el === document.documentElement) return null;
    const s = getComputedStyle(el);
    const outline = s.outlineStyle !== "none" && parseFloat(s.outlineWidth) > 0 && s.outlineColor !== "rgba(0, 0, 0, 0)";
    const label = el.tagName.toLowerCase() + (el.id ? "#" + el.id : "") + ' "' + (el.getAttribute("aria-label") || el.textContent || "").trim().slice(0, 24) + '"';
    return { key: label + [...document.querySelectorAll(el.tagName)].indexOf(el), target: label, ringed: outline || shadowRinged(s.boxShadow) || (el instanceof SVGElement && strokeRinged(s.stroke, s.strokeWidth)) };
  })()`;
  const stops = [];
  const seen = new Set();
  for (let i = 0; i < MAX_TABS; i++) {
    await page.keyboard.press("Tab");
    const stop = await page.evaluate(probe);
    if (!stop) break;
    if (seen.has(stop.key)) break;
    seen.add(stop.key);
    stops.push(stop);
  }
  return stops;
}

/** Every accessibility problem on the page as it is, one line each (the same finding twice counts once). */
export async function auditPage(page, viewport) {
  const found = [...(await runAxe(page)), ...tapProblems(await tapBoxes(page), viewport), ...focusProblems(await focusStops(page))];
  return [...new Set(found)];
}

// --- The baseline: findings known and tolerated, with a reason each. A list that only shrinks. -----------------------------

/** What identifies a finding in the baseline: the problem without what changes from run to run or with a harmless edit.
 *  Svelte's scoped class names (`svelte-1a2b3c`) change with the component's CSS, and the numbers of generated ids
 *  (`bits-c6`, `c22`) with the order things open. A chart shape's coordinates (`rect[y="240.4"]`) move with its data. A
 *  tap target is named by its tag, id and classes alone: not by its label (a row per transaction is one finding, not
 *  one per merchant) or its size (a target made bigger but still short isn't a new finding). */
export const canonical = (problem) => problem
  .replace(/\.svelte-[a-z0-9]+/g, "")
  .replace(/bits-c\d+/g, "bits-c*")
  .replace(/#c\d+(?=[-.\s:"[]|$)/g, "#c*")
  .replace(/"c\d+-/g, '"c*-')
  .replace(/\[([xy])="[\d.]+"\]/g, "[$1]")
  .replace(/^(tap-target: \S+)(?: ".*")? is \d+×\d+ px, under \d+$/, "$1");

/** The baseline file's lines, `<page or flow> @ <width> | <finding> | <reason>`, as { where, problem, reason } entries; # lines
 *  and blank lines are comments. Returns { entries, errors }: a line without all three parts, or listed twice, is an error. */
export function parseBaseline(text) {
  const entries = [];
  const errors = [];
  const seen = new Set();
  for (const [i, raw] of text.split("\n").entries()) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const at = `a11y-baseline.txt line ${i + 1}`;
    const parts = line.split(" | ").map((p) => p.trim());
    if (parts.length < 3 || parts.some((p) => !p)) { errors.push(`${at}: expected "<page or flow> @ <width> | <finding> | <reason>"`); continue; }
    const [where, ...rest] = parts;
    const reason = rest.pop();
    const problem = rest.join(" | ");
    if (!/ @ (phone|desktop|landscape)$/.test(where)) { errors.push(`${at}: "${where}" doesn't end in @ phone, @ desktop or @ landscape`); continue; }
    const key = `${where} | ${problem}`;
    if (seen.has(key)) { errors.push(`${at}: listed twice`); continue; }
    seen.add(key);
    entries.push({ where, problem, reason, key });
  }
  return { entries, errors };
}

export function readBaseline(file = BASELINE) {
  let text = "";
  try { text = readFileSync(file, "utf8"); } catch (e) { if (e.code !== "ENOENT") throw e; }
  return parseBaseline(text);
}

/** Splits the findings of one visit (`where` = "<page or flow> @ <width>") against the baseline: `fresh` is what is not on it
 *  (these fail), `tolerated` how many it covers, `stale` the baseline's lines for this visit that no longer occur (these fail
 *  too, so the list only shrinks). */
export function applyBaseline(found, where, entries) {
  const mine = entries.filter((e) => e.where === where);
  const known = new Set(mine.map((e) => e.problem));
  const seen = new Set(found.map(canonical));
  return {
    fresh: found.filter((p) => !known.has(canonical(p))),
    tolerated: found.filter((p) => known.has(canonical(p))).length,
    stale: mine.filter((e) => !seen.has(e.problem)),
  };
}

/** What a visit reports as problems: each new finding, and each baseline line that no longer occurs. */
export function visitProblems(found, where, entries) {
  const { fresh, stale } = applyBaseline(found, where, entries);
  return [
    ...fresh.map((p) => `${where}: a11y ${p}`),
    ...stale.map((e) => `${where}: a11y-baseline.txt lists "${e.problem}", which no longer occurs: delete the line`),
  ];
}
