#!/usr/bin/env node
// `tools/control`: drive the real app by hand, one command at a time, the way `make verify` drives it in a batch. `start`
// launches a session in the background (control-daemon.mjs): a demo server on fresh demo data under the frozen clock,
// and a Chromium on it, both kept until `stop` (or 30 idle minutes). Every later command connects to that session over a
// socket only this checkout's user can open, does one thing (open a route, click, type, look at the page, take a
// screenshot, run a verify flow) and prints what happened as JSON (`--text` for people).
//
// It only ever drives its own demo server (made-up data on the loopback address), writes only under artifacts/control/
// (ignored by git), never takes make verify's machine lock (a session is long-lived) and refuses to start on a busy machine.
//
// This file is the command line: the commands, their help (references/cli.md is generated from it: `make verify-skill`),
// argument parsing, the state file and the session's lifecycle. It imports nothing outside Node until a command needs the
// browser, so `help` runs anywhere (tools/fleet_checks.py runs it). control-daemon.mjs is the session itself.
import { spawn, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { closeSync, existsSync, mkdirSync, openSync, readdirSync, readFileSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { createConnection } from "node:net";
import { tmpdir } from "node:os";
import { dirname, isAbsolute, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
export const ROOT = resolve(HERE, "../..");
export const ARTIFACTS = join(ROOT, "artifacts");
/** artifacts/control/, or RUNWAY_CONTROL_DIR (the tests' own folder) when that is under artifacts/: nothing else is used. */
export function controlDir(asked, artifacts = ARTIFACTS) {
  const full = asked ? resolve(asked) : "";
  return full.startsWith(`${artifacts}/`) ? full : join(artifacts, "control");
}
export const CONTROL_DIR = controlDir(process.env.RUNWAY_CONTROL_DIR);
export const DAEMON = join(HERE, "control-daemon.mjs");
export const PROG = "tools/control";
export const STATE_VERSION = 1;
export const VIEWPORT_NAMES = ["phone", "desktop"];
/** What make verify seeds besides the plain demo data (runway/verify.py's SEED_OPTIONS, less the signed-in session). */
export const SEED_FLAGS = ["ai-buttons", "investments", "receipt", "subcategories"];
export const DEFAULT_IDLE_MINUTES = 30;

/** A failure the agent can act on: a code to branch on, what went wrong and what to do instead. `exit` 2 is a usage error. */
export class ControlError extends Error {
  constructor(code, message, hint = "", exit = 1) {
    super(message);
    this.code = code;
    this.hint = hint;
    this.exit = exit;
  }
}

const usage = (message, hint) => new ControlError("usage", message, hint, 2);

/** The JSON a failed command prints: never a stack trace. Anything that isn't a ControlError is reported as `internal`. */
export function errorResult(command, e) {
  if (e instanceof ControlError) return { ok: false, command, error: { code: e.code, message: e.message, ...(e.hint ? { hint: e.hint } : {}) }, ...(e.data ?? {}) };
  const first = String(e?.message ?? e).split("\n")[0];
  return { ok: false, command, error: { code: "internal", message: first, hint: `see ${rel(join(CONTROL_DIR, "daemon.log"))} and run \`${PROG} doctor\`` } };
}

// --- The commands ---------------------------------------------------------------------------------------------------
// Each command: its group (the overview lists them by group, so `--help` discloses a little at a time), a one-line
// summary, its arguments ({ name, required, variadic }), its options ({ type: boolean | string | number, help, values,
// default }), a few lines on what it does, and examples. The help, the parser and references/cli.md all come from here.

const DRY_RUN = { type: "boolean", help: "say what it would do, and do nothing" };
const TIMEOUT = { type: "number", help: "how long to wait for the target, in ms (stretched on a busy machine)", default: 10000 };
const NTH = { type: "number", help: "which of several matches to use (0 is the first); without it, more than one match is an error" };
const EXACT = { type: "boolean", help: "match the name or text exactly (case-sensitive), not as a substring" };
const TARGET_HELP = [
  "A target is one of: a role and its accessible name (`button Save`, `link Budget`, `textbox Search`: the names `snapshot` shows),",
  "a selector (`#id`, `.class`, `[data-testid=x]`, `css=…`, `text=…`, `role=…`, `button:has-text('Save')`), or else visible text (`Add transaction`).",
  "Several matches are an error that lists them: pick one with --nth, or name it more exactly.",
];

export const COMMANDS = {
  doctor: {
    group: "session", summary: "Check this machine can run a session, and say how to fix what can't.",
    args: [], options: {},
    details: ["Checks Node, the web app's packages and build, Chromium, Python with Runway's packages, libfaketime (the frozen clock), a free loopback port,",
      "the machine's load, a running make verify, this checkout's session (none, running or stale) and processes a crashed session left.",
      "Each check has ok, detail and, when it fails, fix: the command to run. Exits 1 when a check fails."],
    examples: ["doctor --text"],
  },
  start: {
    group: "session", summary: "Start a session: a demo server on fresh demo data under the frozen clock, and a browser on it.",
    args: [],
    options: {
      "ai-buttons": { type: "boolean", help: "seed what the AI buttons need" },
      investments: { type: "boolean", help: "seed a sample brokerage" },
      receipt: { type: "boolean", help: "seed a store purchase with its receipt" },
      subcategories: { type: "boolean", help: "seed subcategories with transactions in them" },
      plain: { type: "boolean", help: "only the plain demo data (none of the above)" },
      "signed-in": { type: "boolean", help: "sign-in on, the browser signed in (for the app lock and signed_in flows)" },
      viewport: { type: "string", values: VIEWPORT_NAMES, default: "phone", help: "the browser's size to start at" },
      "idle-minutes": { type: "number", default: DEFAULT_IDLE_MINUTES, help: "stop by itself after this long with no command" },
      anyway: { type: "boolean", help: "start even though the machine is busy (waits stretch with the load)" },
    },
    details: ["Without a seed option it seeds what make verify does (ai-buttons, investments, receipt, subcategories), so pages look as in screenshots/.",
      "The server and the seed run under libfaketime at make verify's frozen day, and the browser's clock is fixed at noon that day.",
      "One session per checkout, on ports and data of its own: it never takes make verify's machine lock, and a running make verify is left alone.",
      "Refuses when the machine is busy (make verify's load check) unless --anyway. A stale session (its process gone) is cleaned up first."],
    examples: ["start", "start --viewport desktop", "start --plain --signed-in"],
  },
  stop: {
    group: "session", summary: "Stop the session: its browser, its demo server and its copy of the data.",
    args: [], options: { "dry-run": DRY_RUN },
    details: ["Screenshots under artifacts/control/ stay (cleanup --shots removes them). Stopping when there is no session is not an error."],
    examples: ["stop", "stop --dry-run"],
  },
  cleanup: {
    group: "session", summary: "Remove what a crashed session left: its state file, data folders and processes.",
    args: [], options: { "dry-run": DRY_RUN, shots: { type: "boolean", help: "also delete the screenshots under artifacts/control/" } },
    details: ["Only ever this checkout's control files and processes (found by artifacts/control/ in their command line or data folder).",
      "A running session is left alone: stop it first."],
    examples: ["cleanup --dry-run", "cleanup --shots"],
  },
  reset: {
    group: "session", summary: "Fresh demo data: a new copy of the seed on a new demo server, and the page opened again.",
    args: [], options: { "dry-run": DRY_RUN },
    details: ["What you changed (deleted a transaction, edited a budget) is gone. The browser, viewport and route stay; the port changes."],
    examples: ["reset --dry-run", "reset"],
  },
  goto: {
    group: "navigate", summary: "Open a route of the app.",
    args: [{ name: "route", required: true }], options: { reload: { type: "boolean", help: "then open the app again (a launch): it reloads what it keeps in memory" } },
    details: ["A route is `budget`, `#setup/categories` or `/#transactions`; a full URL only on the session's own server (anything else is refused).",
      "Waits for the page to settle (as wait-settle) and says whether it did.",
      "The app's once-a-minute refresh is off here, as in make verify: after changing data, a page can show what the app had loaded before. --reload shows the server's."],
    examples: ["goto transactions", "goto '#setup/categories'", "goto overview --reload"],
  },
  viewport: {
    group: "navigate", summary: "Resize the browser to the phone (440×956) or desktop (1280×800) size make verify uses.",
    args: [{ name: "size", required: true, values: VIEWPORT_NAMES }], options: {},
    details: ["There is no tablet size (make verify has none): look at 768–1023 px by hand."],
    examples: ["viewport desktop"],
  },
  info: {
    group: "inspect", summary: "Where the browser is: route, title, viewport, the frozen clock and the session.",
    args: [], options: {}, details: [], examples: ["info --text"],
  },
  snapshot: {
    group: "inspect", summary: "The page's accessibility tree as text: roles and the names to click.",
    args: [], options: {
      root: { type: "string", help: "only this part of the page (a selector), e.g. [role=dialog]" },
      "max-lines": { type: "number", default: 300, help: "cut the tree after this many lines (it says so)" },
    },
    details: ["Each line is `- role \"name\"`, indented by nesting: the role and name are what click, type and the role target take."],
    examples: ["snapshot --text", "snapshot --root '[role=dialog]' --text"],
  },
  screenshot: {
    group: "inspect", summary: "Save a screenshot once the page has settled, and print its path.",
    args: [{ name: "path" }],
    options: { full: { type: "boolean", help: "the whole page (the default)" }, viewport: { type: "boolean", help: "only the window, where it's scrolled to" } },
    details: ["Taken as make verify takes one: settled, toasts hidden, endless animations stopped, the pointer moved away, two captures that match.",
      "A full-page shot scrolls to the top first; with a dialog or sheet open it's the window, as make verify does.",
      "The path must be under artifacts/ (default artifacts/control/shots/<n>-<route>-<viewport>.png). Look at it: it is the evidence.",
      "Never copy one into screenshots/: that directory is only what make screenshots writes."],
    examples: ["screenshot", "screenshot --viewport", "screenshot artifacts/control/before.png"],
  },
  "wait-settle": {
    group: "inspect", summary: "Wait until the page has settled, by make verify's rule, and say what is still loading if not.",
    args: [], options: { timeout: { type: "number", default: 20000, help: "how long to wait, in ms (stretched on a busy machine)" } },
    details: ["Settled: no skeleton placeholder, nothing aria-busy, no transition still running, the Investments live prices in, fonts and document loaded."],
    examples: ["wait-settle"],
  },
  click: {
    group: "interact", summary: "Click a target (a role and name, a selector or text).",
    args: [{ name: "target", required: true, variadic: true }], options: { nth: NTH, exact: EXACT, timeout: TIMEOUT },
    details: TARGET_HELP, examples: ["click button Save", "click link Budget --nth 1", "click '[data-tx] button[aria-label^=\"Delete \"]'"],
  },
  type: {
    group: "interact", summary: "Fill a field with text, or pick a select's option (the last argument is the text).",
    args: [{ name: "target", required: true, variadic: true }, { name: "text", required: true }], options: { nth: NTH, exact: EXACT, timeout: TIMEOUT },
    details: [...TARGET_HELP, "The field's current value is replaced. On a select (a combobox with options) it picks the option with that label.",
      "Text that starts with - goes after --: `type textbox Amount -- -5`."],
    examples: ["type textbox Search coffee", "type '#amount' 12.50", "type combobox 'Paid from' 'Everyday Checking'"],
  },
  press: {
    group: "interact", summary: "Press a key, or a chord, in the focused element.",
    args: [{ name: "key", required: true }], options: {},
    details: ["Keys are Playwright's names: Enter, Escape, Tab, Shift+Tab, ArrowDown, Control+a."],
    examples: ["press Escape", "press Shift+Tab"],
  },
  scroll: {
    group: "interact", summary: "Scroll the page: up, down, top, bottom, or until a target is in view.",
    args: [{ name: "where", variadic: true }], options: { nth: NTH, exact: EXACT },
    details: ["Without an argument it scrolls down a screen."], examples: ["scroll", "scroll bottom", "scroll heading Holdings"],
  },
  eval: {
    group: "interact", summary: "Evaluate JavaScript in the page and print the result (read-only unless --write).",
    args: [{ name: "js", required: true, variadic: true }],
    options: { write: { type: "boolean", help: "allow it to change the app's data (requests other than GET)" } },
    details: ["An expression (`document.title`) or a function (`() => [...document.querySelectorAll('h2')].map(h => h.textContent)`); the result as JSON.",
      "Read-only by default: a request it makes other than GET or HEAD is blocked, and a change it makes to the page is reported (domChanges).",
      "Prefer snapshot, click and type: they act as a person would."],
    examples: ["eval document.title", "eval \"() => getComputedStyle(document.body).backgroundColor\""],
  },
  console: {
    group: "observe", summary: "The console messages and page errors since the last call.",
    args: [], options: {}, details: ["make verify fails a page on a console error or an uncaught page error: so should you."],
    examples: ["console --text"],
  },
  network: {
    group: "observe", summary: "The requests since the last call: method, path, status, and the ones that failed.",
    args: [], options: {}, details: ["Paths only: no query strings, bodies or ids (numbers become :id). make verify fails a page on a 5xx."],
    examples: ["network --text"],
  },
  flows: {
    group: "flows", summary: "List make verify's flows (frontend/verify/flows/), with the page and widths each starts on.",
    args: [], options: {}, details: [], examples: ["flows --text"],
  },
  flow: {
    group: "flows", summary: "Run one verify flow on the session, step by step, and report each step.",
    args: [{ name: "name", required: true }], options: { viewport: { type: "string", values: VIEWPORT_NAMES, help: "resize to this first (default: the session's)" } },
    details: ["Its screenshots go to artifacts/control/flows/, never artifacts/verify/ or screenshots/. It stops at the first failed step, with a -FAILED- image.",
      "A flow starts from the demo data as make verify seeds it: run `reset` first if you changed anything. A signed_in flow needs `start --signed-in`."],
    examples: ["flow budget-edit", "flow transactions-delete --viewport desktop"],
  },
  help: {
    group: "help", summary: "This overview, or one command's help (`help <command>`, the same as `<command> --help`).",
    args: [{ name: "command" }], options: { markdown: { type: "boolean", help: "the whole reference as Markdown (references/cli.md)" } },
    details: [], examples: ["help click"],
  },
};

const GROUPS = [
  ["session", "Session"], ["navigate", "Navigate"], ["inspect", "Inspect"], ["interact", "Interact"], ["observe", "Observe"], ["flows", "Flows"], ["help", "Help"],
];
/** The commands that run in the session (sent to control-daemon.mjs); the others run here. */
export const SESSION_COMMANDS = ["goto", "viewport", "info", "snapshot", "screenshot", "wait-settle", "click", "type", "press", "scroll", "eval", "console", "network", "flow", "reset"];

// --- Help -----------------------------------------------------------------------------------------------------------

const argUsage = (a) => {
  const name = a.values ? a.values.join("|") : a.name;
  return a.required ? `<${name}${a.variadic ? "…" : ""}>` : `[${name}${a.variadic ? "…" : ""}]`;
};

export function commandUsage(name) {
  const c = COMMANDS[name];
  const opts = Object.keys(c.options).length ? " [options]" : "";
  return `${PROG} ${name}${c.args.map((a) => ` ${argUsage(a)}`).join("")}${opts}`;
}

export function commandHelp(name) {
  const c = COMMANDS[name];
  const lines = [`${commandUsage(name)}`, "", c.summary];
  if (c.details.length) lines.push("", ...c.details);
  const opts = Object.entries(c.options);
  if (opts.length) {
    lines.push("", "Options:");
    for (const [o, spec] of opts) {
      const value = spec.type === "boolean" ? "" : ` <${spec.values ? spec.values.join("|") : spec.type}>`;
      lines.push(`  --${o}${value}  ${spec.help}${spec.default !== undefined ? ` (default ${spec.default})` : ""}`);
    }
  }
  if (c.examples.length) lines.push("", "Examples:", ...c.examples.map((e) => `  ${PROG} ${e}`));
  return lines.join("\n");
}

export function overviewHelp() {
  const lines = [
    `${PROG}: drive the real app on demo data, one command at a time, and prove what you changed.`,
    "",
    `Usage: ${PROG} <command> [arguments] [options]`,
    "",
    "Start with `doctor`, then `start`; finish with `stop`. Output is JSON ({\"ok\": true, …}, or {\"ok\": false, \"error\": {code, message, hint}});",
    "--text prints it for people. Exit 0 on success, 1 when the command failed, 2 for a usage error. `<command> --help` says more.",
  ];
  for (const [group, title] of GROUPS) {
    lines.push("", `${title}:`);
    for (const [name, c] of Object.entries(COMMANDS)) if (c.group === group) lines.push(`  ${name.padEnd(12)} ${c.summary}`);
  }
  lines.push("", "Localhost and demo data only: it drives only the demo server it started, and writes only under artifacts/control/ (never committed).");
  return lines.join("\n");
}

/** references/cli.md: the overview and every command's help, as `--help` prints them. */
export function markdownReference() {
  const out = [
    "# tools/control: command reference",
    "",
    "Generated from the CLI's own help by `make verify-skill` (`node frontend/verify/control.mjs help --markdown`); `make fleet-checks` fails when it is out of date. Don't edit it by hand.",
    "",
    "```text", overviewHelp(), "```",
  ];
  for (const [group, title] of GROUPS) {
    out.push("", `## ${title}`);
    for (const name of Object.keys(COMMANDS)) if (COMMANDS[name].group === group) out.push("", `### ${name}`, "", "```text", commandHelp(name), "```");
  }
  return `${out.join("\n")}\n`;
}

// --- Parsing --------------------------------------------------------------------------------------------------------

const distance = (a, b) => {
  const d = Array.from({ length: a.length + 1 }, (_, i) => [i, ...Array(b.length).fill(0)]);
  for (let j = 1; j <= b.length; j++) d[0][j] = j;
  for (let i = 1; i <= a.length; i++) for (let j = 1; j <= b.length; j++) d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
  return d[a.length][b.length];
};

/** The known name closest to `word`, when one is close enough to be what was meant. */
export function closest(word, names) {
  const best = names.map((n) => [n, n.startsWith(word) ? 0 : distance(word, n)]).sort((x, y) => x[1] - y[1])[0];
  return best && best[1] <= Math.max(2, Math.floor(word.length / 3)) ? best[0] : null;
}

/** argv (after the program) → { command, args, options, text, help }. Throws a usage ControlError saying what to type instead. */
export function parseArgs(argv) {
  const out = { command: null, args: [], options: {}, text: false, help: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--") { out.args.push(...argv.slice(i + 1)); break; }
    if (a === "--text") { out.text = true; continue; }
    if (a === "--json") { out.text = false; continue; }
    if (a === "--help" || a === "-h") { out.help = true; continue; }
    if (out.command === null && !a.startsWith("-")) {
      if (!(a in COMMANDS)) {
        const near = closest(a, Object.keys(COMMANDS));
        throw usage(`unknown command "${a}"`, near ? `did you mean \`${PROG} ${near}\`? (\`${PROG} --help\` lists them)` : `\`${PROG} --help\` lists the commands`);
      }
      out.command = a;
      continue;
    }
    if (a.startsWith("--") && a.length > 2) {
      const eq = a.indexOf("=");
      const name = eq < 0 ? a.slice(2) : a.slice(2, eq);
      const inline = eq < 0 ? undefined : a.slice(eq + 1);
      const spec = out.command ? COMMANDS[out.command].options[name] : undefined;
      if (!spec) {
        const known = out.command ? Object.keys(COMMANDS[out.command].options) : [];
        const near = closest(name, known);
        throw usage(out.command ? `${out.command} has no option --${name}` : `--${name} goes after a command`,
          near ? `did you mean --${near}? (\`${PROG} ${out.command} --help\`)` : out.command ? `its options: ${known.map((k) => `--${k}`).join(", ") || "none"}; text starting with - goes after --` : `\`${PROG} --help\``);
      }
      if (spec.type === "boolean") {
        if (inline !== undefined) throw usage(`--${name} takes no value`, `write --${name} alone`);
        out.options[name] = true;
        continue;
      }
      const value = inline ?? argv[++i];
      if (value === undefined) throw usage(`--${name} needs a value`, `--${name} <${spec.values ? spec.values.join("|") : spec.type}>`);
      if (spec.type === "number" && !(value.trim() !== "" && Number.isFinite(Number(value)) && Number(value) >= 0)) throw usage(`--${name} is a number of 0 or more, not "${value}"`, `e.g. --${name} ${spec.default ?? 1}`);
      if (spec.values && !spec.values.includes(value)) throw usage(`--${name} is ${spec.values.join(" or ")}, not "${value}"`, `e.g. --${name} ${spec.values[0]}`);
      out.options[name] = spec.type === "number" ? Number(value) : value;
      continue;
    }
    if (out.command === null) throw usage(`"${a}" isn't a command or an option`, `\`${PROG} --help\` lists the commands`);
    out.args.push(a);
  }
  if (out.command === null || out.help) return out;
  const c = COMMANDS[out.command];
  for (const [name, spec] of Object.entries(c.options)) if (spec.default !== undefined && !(name in out.options)) out.options[name] = spec.default;
  const required = c.args.filter((a) => a.required).length;
  const variadic = c.args.some((a) => a.variadic);
  if (out.args.length < required) throw usage(`${out.command} needs ${c.args.filter((a) => a.required).map(argUsage).join(" ")}`, `usage: ${commandUsage(out.command)}`);
  if (!variadic && out.args.length > c.args.length) throw usage(`${out.command} takes ${c.args.length ? c.args.map(argUsage).join(" ") : "no arguments"}, not "${out.args.join(" ")}"`, `usage: ${commandUsage(out.command)} (quote an argument with spaces)`);
  for (const [i, a] of c.args.entries()) if (a.values && out.args[i] !== undefined && !a.values.includes(out.args[i])) throw usage(`${out.command} takes ${a.values.join(" or ")}, not "${out.args[i]}"`, `usage: ${commandUsage(out.command)}`);
  if (out.command === "screenshot" && out.options.full && out.options.viewport) throw usage("screenshot takes --full or --viewport, not both", "--full is the default");
  if (out.command === "start" && out.options.plain && SEED_FLAGS.some((f) => out.options[f])) throw usage("--plain is the demo data without the seed options; it can't be combined with them", "drop --plain, or the seed options");
  return out;
}

/** The seed options a `start` asks for: the ones named; none with --plain; make verify's when none is named. */
export function seedOptions(options) {
  if (options.plain) return [];
  const named = SEED_FLAGS.filter((f) => options[f]);
  return named.length ? named : [...SEED_FLAGS];
}

// --- Targets, routes and what's reported ----------------------------------------------------------------------------

/** The ARIA roles a target can start with (`button Save`). */
export const ROLES = ["alert", "alertdialog", "article", "banner", "button", "cell", "checkbox", "columnheader", "combobox", "complementary", "contentinfo",
  "dialog", "figure", "form", "grid", "gridcell", "group", "heading", "img", "link", "list", "listbox", "listitem", "main", "menu", "menubar", "menuitem",
  "menuitemcheckbox", "menuitemradio", "navigation", "option", "progressbar", "radio", "radiogroup", "region", "row", "rowheader", "searchbox", "separator",
  "slider", "spinbutton", "status", "switch", "tab", "table", "tablist", "tabpanel", "textbox", "toolbar", "tooltip", "tree", "treeitem"];

/** A target's words → { kind: "role", role, name? } | { kind: "selector", selector } | { kind: "text", text } (TARGET_HELP). */
export function parseTarget(words) {
  const raw = words.join(" ").trim();
  if (!raw) throw usage("no target given", TARGET_HELP.join(" "));
  if (ROLES.includes(words[0])) return { kind: "role", role: words[0], ...(words.length > 1 ? { name: words.slice(1).join(" ") } : {}) };
  if (/^(css|text|role|xpath|id|data-testid|internal:[\w-]+)=/.test(raw) || /^[#.[]/.test(raw)
    || /^[a-z][\w-]*(\[|\.[\w-]|#[\w-]|:[a-z-]+\(|\s*>\s)/i.test(raw)) return { kind: "selector", selector: raw };
  return { kind: "text", text: raw };
}

/** A route (`budget`, `#setup/categories`, `/#transactions`) as a URL on the session's server; a full URL only on it. */
export function routeUrl(base, route) {
  if (/^[a-z][a-z0-9+.-]*:/i.test(route)) {
    let url;
    try { url = new URL(route); } catch { throw new ControlError("bad_route", `${route} isn't a URL`, "give a route like budget or #setup/categories"); }
    if (url.origin !== new URL(base).origin) throw new ControlError("refused", `${url.origin} isn't this session's demo server`, `the session drives only ${base}: give a route like budget or #setup/categories`);
    return url.href;
  }
  if (route.startsWith("/")) return `${base}${route}`;
  return `${base}/${route.startsWith("#") ? route : `#${route}`}`;
}

/** A request's URL as reported: its path on the session's server (host and path elsewhere), ids as :id, never the query
 *  string or hash (the private-data-in-logs rule, demo data or not). null for data: and blob: URLs. */
export function scrubUrl(url, base) {
  let u;
  try { u = new URL(url); } catch { return "(not a URL)"; }
  if (u.protocol === "data:" || u.protocol === "blob:") return null;
  const path = u.pathname.split("/").map((s) => (/^\d+$|^[0-9a-f]{12,}$|^[0-9a-f-]{36}$/i.test(s) ? ":id" : s)).join("/");
  return u.origin === new URL(base).origin ? path : `${u.host}${path}`;
}

/** A console message as reported: query strings, amounts, email addresses and long tokens taken out. */
export function scrubText(text) {
  return String(text)
    .replace(/(\bhttps?:\/\/[^\s?#"']+)[?#][^\s"']*/g, "$1?…")
    .replace(/[$€£]\s?-?\d[\d,]*(\.\d+)?|-?\d[\d,]*\.\d{2}\b/g, "<amount>")
    .replace(/[\w.+-]+@[\w-]+\.[\w.-]+/g, "<email>")
    .replace(/\b[A-Za-z0-9_-]{32,}\b/g, "<token>");
}

/** `eval`'s code as the expression the page evaluates: a function (`() => …`, `async () => …`, `function () {…}`) is called
 *  (its result awaited), anything else is evaluated as it is. */
export function jsExpression(code) {
  return /^\s*(async\s+)?(\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>|^\s*(async\s+)?function\b/.test(code) ? `(${code})()` : code;
}

/** A route as part of a screenshot's file name: `#setup/categories` → setup-categories. */
export const shotName = (route) => route.replace(/^\/?#?/, "").replace(/[^\w-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40) || "overview";

/** The highest number among numbered screenshots (`007-budget-phone.png`), 0 for none: a new one takes the next, so it
 *  never overwrites one that's there, gaps or not. */
export const lastShotNumber = (names) => names.reduce((max, n) => Math.max(max, /^(\d{3,})-/.exec(n)?.[1] ?? 0), 0);

// --- The state file -------------------------------------------------------------------------------------------------
// artifacts/control/state.json: the session's daemon (pid), its socket and token, its server and what it was seeded with.
// The daemon writes it (status "starting", then "ready" or "failed") and removes it when it stops. One whose daemon is gone
// (killed, the machine restarted) is stale: commands say so, `start` and `cleanup` clear it.

export const statePath = (dir = CONTROL_DIR) => join(dir, "state.json");

/** The state, or null when there is none; { corrupt: true } for a file that isn't a state. */
export function readState(dir = CONTROL_DIR) {
  let text;
  try { text = readFileSync(statePath(dir), "utf8"); } catch { return null; /* no session */ }
  try {
    const state = JSON.parse(text);
    return state && state.version === STATE_VERSION && Number.isInteger(state.pid) ? state : { corrupt: true };
  } catch { return { corrupt: true }; /* half written by a process that died: stale */ }
}

/** Writes the state atomically (a new file renamed into place), readable by this user only: it holds the socket's token. */
export function writeState(state, dir = CONTROL_DIR) {
  mkdirSync(dir, { recursive: true });
  const tmp = `${statePath(dir)}.${process.pid}.tmp`;
  writeFileSync(tmp, JSON.stringify({ version: STATE_VERSION, ...state }, null, 2), { mode: 0o600 });
  renameSync(tmp, statePath(dir));
}

export function removeState(dir = CONTROL_DIR) { rmSync(statePath(dir), { force: true }); }

/** A process's arguments from /proc ([] when they can't be read: it's gone, or this isn't Linux). */
const argsOf = (pid, proc) => { try { return readFileSync(`${proc}/${pid}/cmdline`, "utf8").split("\0"); } catch { return []; /* the process is gone */ } };
const argAfter = (args, flag) => { const i = args.indexOf(flag); return i < 0 ? undefined : args[i + 1]; };
const daemonFor = (args, dir) => args.some((a) => a.endsWith("/control-daemon.mjs")) && argAfter(args, "--dir") === dir;

/** Whether `pid` is this checkout's control daemon for `dir` (not a process that took its number since). */
export function isOurDaemon(pid, dir = CONTROL_DIR, proc = "/proc") {
  return daemonFor(argsOf(pid, proc), dir);
}

/** none, corrupt, stale (its daemon gone), starting, ready or failed (the daemon said why, and is ending). */
export function sessionStatus(state, { dir = CONTROL_DIR, isDaemon = isOurDaemon } = {}) {
  if (!state) return "none";
  if (state.corrupt) return "corrupt";
  if (!isDaemon(state.pid, dir)) return "stale";
  return ["starting", "ready", "failed"].includes(state.status) ? state.status : "corrupt";
}

/** The socket a session listens on: in artifacts/control/, or in the temp folder when that path is too long for a socket. */
export function socketPath(dir = CONTROL_DIR) {
  const inside = join(dir, "control.sock");
  return Buffer.byteLength(inside) <= 100 ? inside : join(tmpdir(), `runway-control-${createHash("sha256").update(dir).digest("hex").slice(0, 16)}.sock`);
}

/** The session's state, or the error that says why there's none to use and what to do. */
export function requireSession(dir = CONTROL_DIR, opts = {}) {
  const state = readState(dir);
  const status = sessionStatus(state, { dir, ...opts });
  if (status === "ready") return state;
  if (status === "none") throw new ControlError("no_session", "no session is running", `run \`${PROG} start\` first (\`${PROG} doctor\` if it fails)`);
  if (status === "starting") throw new ControlError("starting", "the session is still starting", "run the command again in a few seconds");
  if (status === "failed") throw new ControlError("start_failed", `the session failed to start: ${state.error ?? "see the log"}`, `run \`${PROG} cleanup\`, then \`${PROG} doctor\``);
  throw new ControlError("stale_session", "the session's state file is stale: its process is gone (it crashed, was killed or the machine restarted)",
    `run \`${PROG} cleanup\` then \`${PROG} start\` (start alone also clears it)`);
}

// --- Processes and leftovers ----------------------------------------------------------------------------------------

const environ = (pid, proc) => { try { return readFileSync(`${proc}/${pid}/environ`, "utf8").split("\0"); } catch { return []; /* gone, or another user's */ } };
const parentOf = (pid, proc) => { try { return Number(readFileSync(`${proc}/${pid}/stat`, "utf8").split(")").pop().trim().split(/\s+/)[1]); } catch { return 0; /* gone */ } };

/** This checkout's control processes: a daemon (--dir in its command line), a demo server or seed (its data, RUNWAY_DATA,
 *  under `dir`, or `serve --data` there). Never anything else: the match is on this checkout's own artifacts/control/. */
export function controlProcesses(dir = CONTROL_DIR, proc = "/proc") {
  let pids;
  try { pids = readdirSync(proc).filter((p) => /^\d+$/.test(p)).map(Number); } catch { return []; /* no /proc: nothing to find */ }
  const out = [];
  for (const pid of pids) {
    if (pid === process.pid) continue;
    const args = argsOf(pid, proc);
    if (!args.length) continue;
    const daemon = daemonFor(args, dir);
    const data = environ(pid, proc).find((v) => v.startsWith("RUNWAY_DATA="))?.slice("RUNWAY_DATA=".length) ?? argAfter(args, "--data") ?? "";
    if (daemon || data.startsWith(`${dir}/`)) out.push({ pid, parent: parentOf(pid, proc), what: daemon ? "control daemon" : "demo server", command: args.join(" ").trim().slice(0, 120) });
  }
  return out;
}

/** What cleanup removes and stops. With a live session (`live`: its daemon's pid) only what isn't that session's: other
 *  session folders and processes that aren't it or its children. Without one: the state file, the socket, a start lock
 *  whose process is gone, every session folder and every control process. `shots`: the screenshots too. */
export function cleanupPlan({ dir = CONTROL_DIR, state = null, live = null, processes = [], shots = false, entries = null, alive = processAlive } = {}) {
  let names;
  try { names = entries ?? readdirSync(dir); } catch { names = []; /* no artifacts/control yet */ }
  const remove = [];
  const mine = live && state?.sessionDir ? state.sessionDir : null;
  for (const n of names) {
    const p = join(dir, n);
    if (n.startsWith("session-") && p !== mine) remove.push(p);
    if (!live && ["state.json", "control.sock"].includes(n)) remove.push(p);
    if (!live && n.endsWith(".tmp")) remove.push(p);
    if (n === "start.lock" && !lockHeld(p, alive)) remove.push(p);
    if (shots && ["shots", "flows"].includes(n)) remove.push(p);
  }
  if (!live && state?.socket && !state.socket.startsWith(dir) && !remove.includes(state.socket)) remove.push(state.socket);
  const kill = processes.filter((p) => !live || (p.pid !== live && p.parent !== live));
  return { remove, kill };
}

export function processAlive(pid) {
  try { process.kill(pid, 0); return true; } catch (e) { return e.code === "EPERM"; }
}

function lockHeld(path, alive = processAlive) {
  try { return alive(Number(readFileSync(path, "utf8").trim())); } catch { return false; /* unreadable: nobody holds it */ }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitGone(pid, ms) {
  const end = Date.now() + ms;
  while (processAlive(pid) && Date.now() < end) await sleep(100);
  return !processAlive(pid);
}

/** Stops each process: SIGTERM, then SIGKILL after 5 seconds. */
async function stopProcesses(list) {
  for (const p of list) { try { process.kill(p.pid, "SIGTERM"); } catch { /* already gone */ } }
  for (const p of list) if (!(await waitGone(p.pid, 5000))) { try { process.kill(p.pid, "SIGKILL"); } catch { /* gone meanwhile */ } }
}

export const rel = (p) => (p.startsWith(ROOT) ? relative(ROOT, p) || "." : p);

async function applyCleanup(plan) {
  await stopProcesses(plan.kill);
  for (const p of plan.remove) rmSync(p, { recursive: true, force: true });
  return { removed: plan.remove.map(rel), killed: plan.kill.map((p) => ({ pid: p.pid, what: p.what })) };
}

// --- Talking to the session -----------------------------------------------------------------------------------------

/** Sends one command to the session and returns its reply. The token in the state file proves this caller may use it. */
export function rpc(state, request, timeout = 120000) {
  return new Promise((resolve, reject) => {
    const socket = createConnection(state.socket);
    let data = "";
    const timer = setTimeout(() => { socket.destroy(); reject(new ControlError("timeout", `the session didn't answer within ${Math.round(timeout / 1000)} seconds`, `run \`${PROG} info\`; if that hangs too, \`${PROG} stop\` and \`${PROG} start\``)); }, timeout);
    socket.on("connect", () => socket.write(`${JSON.stringify({ token: state.token, ...request })}\n`));
    socket.on("data", (d) => { data += d; });
    socket.on("end", () => {
      clearTimeout(timer);
      try { resolve(JSON.parse(data)); } catch { reject(new ControlError("session_error", "the session closed the connection without an answer", `see ${rel(join(CONTROL_DIR, "daemon.log"))}; \`${PROG} stop\` and \`${PROG} start\``)); }
    });
    socket.on("error", (e) => {
      clearTimeout(timer);
      reject(new ControlError("stale_session", `can't reach the session (${e.code ?? e.message})`, `run \`${PROG} cleanup\` then \`${PROG} start\``));
    });
  });
}

// --- Checks before a start (and doctor) -----------------------------------------------------------------------------

/** The Python with Runway's packages: RUNWAY_PYTHON, or Poetry's environment for this checkout. */
export function findPython(env = process.env, run = spawnSync) {
  if (env.RUNWAY_PYTHON) return env.RUNWAY_PYTHON;
  const r = run("poetry", ["env", "info", "--executable"], { cwd: ROOT, encoding: "utf8" });
  return r.status === 0 && r.stdout?.trim() ? r.stdout.trim() : null;
}

/** runway/verify.py's `status`: libfaketime, the frozen day, how busy the machine is and whether make verify is running. */
export function machineStatus(python, run = spawnSync) {
  const r = run(python, ["-m", "runway.verify", "status"], { cwd: ROOT, encoding: "utf8", timeout: 60000 });
  try { return JSON.parse(r.stdout); } catch {
    throw new ControlError("python", `${python} -m runway.verify status failed: ${(r.stderr || r.error?.message || "").trim().split("\n").pop()}`,
      "install Runway's Python packages: poetry install (or set RUNWAY_PYTHON to a Python that has them)");
  }
}

const has = (p) => existsSync(join(ROOT, p));

/** The checks `doctor` makes (and `start` the ones it needs), each { name, ok, detail, fix? }. */
export async function doctorChecks({ python = findPython(), status } = {}) {
  const checks = [];
  const add = (name, ok, detail, fix) => checks.push({ name, ok, detail, ...(ok || !fix ? {} : { fix }) });
  add("node", true, process.version);
  const packages = has("frontend/node_modules/@playwright/test/package.json");
  add("web app packages", packages, packages ? "frontend/node_modules has @playwright/test" : "frontend/node_modules is missing or incomplete", "cd frontend && npm ci");
  const built = has("runway/static/app/index.html");
  add("web app build", built, built ? "runway/static/app/index.html" : "the web app isn't built", "cd frontend && npm run build (rerun after changing frontend/src)");
  if (packages) {
    const { chromium } = await import("@playwright/test");
    const { findChromium } = await import("./verify.mjs");
    const exe = findChromium() ?? chromium.executablePath();
    add("chromium", existsSync(exe), exe, "cd frontend && npx playwright install chromium");
  } else add("chromium", false, "can't look without the web app's packages", "cd frontend && npm ci");
  add("python", !!python, python ?? "no Python with Runway's packages", "poetry install (or set RUNWAY_PYTHON)");
  if (python && !status) { try { status = machineStatus(python); } catch (e) { add("runway.verify", false, e.message, e.hint); } }
  if (status) {
    add("libfaketime", !!status.libfaketime, status.libfaketime ?? "not installed: the clock can't be frozen", status.libfaketime_install);
    add("frozen clock", true, `server, seed and browser at ${status.frozen_time} (runway/verify.py's FROZEN_DAY)`);
    add("machine load", !status.busy, `${status.busy_per_cpu} busy per CPU (start refuses above ${status.busy_limit}) on ${status.cpus} CPUs`,
      "wait for the other work to finish, or start --anyway (waits stretch with the load)");
    add("make verify", true, status.verify_running ? `running (${status.verify_running}): a session uses other ports and data and never takes its lock, but slows it down` : "not running");
  }
  try {
    const { freePort } = await import("./jobs.mjs");
    add("free port", true, `127.0.0.1:${await freePort()} is free (a session takes a new free port each start)`);
  } catch (e) { add("free port", false, `can't listen on the loopback address (${e.message})`, "check nothing blocks 127.0.0.1"); }
  const state = readState();
  const s = sessionStatus(state);
  add("session", !["stale", "corrupt", "failed"].includes(s), s === "ready" ? `running (pid ${state.pid}) at ${state.base}` : s === "none" ? "none" : `${s}: its process is gone or it failed`, `${PROG} cleanup`);
  const live = s === "ready" || s === "starting" ? state.pid : null;
  const leftovers = cleanupPlan({ state, live, processes: controlProcesses() }).kill;
  add("leftover processes", !leftovers.length, leftovers.length ? leftovers.map((p) => `${p.what} pid ${p.pid}`).join(", ") : "none", `${PROG} cleanup`);
  try {
    mkdirSync(CONTROL_DIR, { recursive: true });
    add("artifacts/control", true, `${rel(CONTROL_DIR)} is writable (ignored by git)`);
  } catch (e) { add("artifacts/control", false, e.message, `check you can write to ${ARTIFACTS}`); }
  return checks;
}

// --- The commands that run here -------------------------------------------------------------------------------------

/** Holds artifacts/control/start.lock while a start runs, so two starts at once don't make two sessions. */
function takeStartLock() {
  const path = join(CONTROL_DIR, "start.lock");
  for (let tries = 0; tries < 2; tries++) {
    try {
      const fd = openSync(path, "wx", 0o600);
      writeFileSync(fd, String(process.pid));
      closeSync(fd);
      return () => rmSync(path, { force: true });
    } catch (e) {
      if (e.code !== "EEXIST") throw e;
      if (lockHeld(path)) throw new ControlError("already_starting", "another start is running in this checkout", `wait for it, then \`${PROG} info\``);
      rmSync(path, { force: true });   // its process is gone
    }
  }
  throw new ControlError("already_starting", "couldn't take artifacts/control/start.lock", `\`${PROG} cleanup\``);
}

// The end of a log, without its terminal colors (Playwright's call log has them) or blank lines.
// eslint-disable-next-line no-control-regex -- the escape character is what a color code starts with
const ANSI = /\u001b\[[0-9;]*m/g;
const tail = (file, n = 15) => { try { return readFileSync(file, "utf8").replace(ANSI, "").split("\n").filter((l) => l.trim()).slice(-n).join("\n"); } catch { return ""; /* no log yet */ } };

async function start(options) {
  mkdirSync(CONTROL_DIR, { recursive: true });
  // The lock first, then the look at the state: two starts at once can't both find no session and both launch one.
  const release = takeStartLock();
  try { return await startLocked(options); } finally { release(); }
}

async function startLocked(options) {
  const before = readState();
  const was = sessionStatus(before);
  if (was === "ready" || was === "starting") {
    throw new ControlError("already_running", `a session is already ${was === "ready" ? `running at ${before.base}` : "starting"} (pid ${before.pid})`, `use it (\`${PROG} info\`), or \`${PROG} stop\` first to start another`);
  }
  const notes = [];
  if (was !== "none") {
    const done = await applyCleanup(cleanupPlan({ state: before, processes: controlProcesses() }));
    notes.push(`cleaned up a stale session (${done.removed.length} files, ${done.killed.length} processes)`);
  }
  const python = findPython();
  let status = null;
  try { status = python ? machineStatus(python) : null; } catch { /* doctorChecks says why */ }
  const checks = await doctorChecks({ python, status });
  const blocking = checks.filter((c) => !c.ok && !["machine load", "session", "leftover processes"].includes(c.name));
  if (blocking.length || !status) throw new ControlError("not_ready", `this machine can't start a session: ${blocking.map((c) => `${c.name} (${c.detail})`).join("; ")}`, blocking.map((c) => c.fix).filter(Boolean).join("; "));
  if (status.busy && !options.anyway) {
    throw new ControlError("machine_busy", `the machine is busy (${status.busy_per_cpu} per CPU, more than ${status.busy_limit}): pages would load slowly and waits could time out`,
      `wait a minute and run \`${PROG} start\` again, or \`${PROG} start --anyway\``);
  }
  if (status.busy) notes.push(`the machine is busy (${status.busy_per_cpu} per CPU): waits are stretched`);
  if (status.verify_running) notes.push(`make verify is running (${status.verify_running}); this session doesn't touch it`);
  const seed = seedOptions(options);
  const log = openSync(join(CONTROL_DIR, "daemon.log"), "w", 0o600);
  const child = spawn(process.execPath, [DAEMON, "--dir", CONTROL_DIR, "--python", python, "--day", status.frozen_day, "--viewport", options.viewport,
    "--idle-minutes", String(options["idle-minutes"]), "--seed", seed.join(","), ...(options["signed-in"] ? ["--signed-in"] : [])],
  { cwd: ROOT, detached: true, stdio: ["ignore", log, log] });
  closeSync(log);
  child.unref();
  const end = Date.now() + 180000;
  for (;;) {
    await sleep(250);
    const state = readState();
    if (state?.pid === child.pid && state.status === "ready") {
      return { started: true, url: state.base, route: state.route, viewport: state.viewport, seed: state.seed, signedIn: state.signedIn, clock: state.clock,
        pid: state.pid, idleMinutes: state.idleMinutes, notes, next: [`${PROG} snapshot --text`, `${PROG} screenshot`, `${PROG} goto <route>`, `${PROG} stop`] };
    }
    const gone = !processAlive(child.pid);
    if (state?.status === "failed" || gone || Date.now() > end) {
      if (!gone) { try { process.kill(child.pid, "SIGTERM"); } catch { /* ended meanwhile */ } await waitGone(child.pid, 10000); }
      await applyCleanup(cleanupPlan({ processes: controlProcesses() }));
      throw new ControlError("start_failed", `the session didn't start: ${state?.error ?? (gone ? "its process ended" : "it wasn't ready after 3 minutes")}`,
        `the end of ${rel(join(CONTROL_DIR, "daemon.log"))}: ${tail(join(CONTROL_DIR, "daemon.log"), 4).replace(/\s*\n\s*/g, " | ")} — run \`${PROG} doctor\``);
    }
  }
}

async function stop(options) {
  const state = readState();
  const s = sessionStatus(state);
  if (s === "none") return { stopped: false, message: "no session was running" };
  if (s !== "ready" && s !== "starting" && s !== "failed") {
    const plan = cleanupPlan({ state, processes: controlProcesses() });
    if (options["dry-run"]) return { dryRun: true, stale: true, wouldRemove: plan.remove.map(rel), wouldStop: plan.kill.map(({ pid, what }) => ({ pid, what })) };
    return { stopped: false, stale: true, ...(await applyCleanup(plan)) };
  }
  if (options["dry-run"]) {
    return { dryRun: true, wouldStop: { pid: state.pid, server: state.base, data: rel(state.sessionDir ?? CONTROL_DIR) }, keeps: ["artifacts/control/shots/", "artifacts/control/flows/"] };
  }
  await rpc(state, { command: "stop" }, 20000).catch(() => { /* not answering: it's stopped by signal below */ });
  if (!(await waitGone(state.pid, 20000))) await stopProcesses([{ pid: state.pid }]);
  const rest = await applyCleanup(cleanupPlan({ processes: controlProcesses() }));
  return { stopped: true, pid: state.pid, ...rest };
}

async function cleanup(options) {
  const state = readState();
  const s = sessionStatus(state);
  const live = s === "ready" || s === "starting" ? state.pid : null;
  const plan = cleanupPlan({ state, live, processes: controlProcesses(), shots: !!options.shots });
  const note = live ? `the running session (pid ${live}) is left alone: \`${PROG} stop\` stops it` : undefined;
  if (options["dry-run"]) return { dryRun: true, wouldRemove: plan.remove.map(rel), wouldStop: plan.kill.map(({ pid, what, command }) => ({ pid, what, command })), ...(note ? { note } : {}) };
  return { ...(await applyCleanup(plan)), ...(note ? { note } : {}) };
}

async function listFlows() {
  const { readFlows } = await import("./verify.mjs");
  const { flows, error } = readFlows(join(HERE, "flows"));
  if (error) throw new ControlError("bad_flow", error, "fix the flow file (frontend/verify/flows/README.md has the format)");
  return { flows: flows.map((f) => ({ name: f.name, page: f.page ?? "overview", viewports: f.viewports ?? ["phone", "desktop"], signedIn: !!f.signed_in, steps: f.steps.length })) };
}

/** What `reset --dry-run` says, from the state alone (nothing is sent to the session). */
function resetPlan(state) {
  return { dryRun: true, wouldDo: [`stop the demo server at ${state.base} and delete its copy of the data`, "start a new one on a fresh copy of the seed (a new port)",
    "open the same route again in the same browser"], seed: state.seed };
}

/** Where a screenshot path goes: under artifacts/ only (relative paths are from the repository's root). */
export function screenshotTarget(path, root = ROOT) {
  if (!path) return null;
  const full = isAbsolute(path) ? resolve(path) : resolve(root, path);
  const base = join(root, "artifacts");
  if (!full.startsWith(`${base}/`)) throw new ControlError("outside_artifacts", `${path} isn't under artifacts/`, "give a path under artifacts/ (artifacts/control/… by default): nothing it writes may be committed");
  if (!full.endsWith(".png")) throw new ControlError("not_png", `${path} doesn't end in .png`, "screenshots are PNG files: name it <something>.png");
  return full;
}

/** The session's reply as a command's data, or the error it reports. A failure keeps the reply's other fields (a failed
 *  flow's steps, its -FAILED- image and notes): they are what says where it went wrong. */
export function replyData(reply) {
  const { ok, error, command: _command, ...rest } = reply ?? {};
  if (ok) return rest;
  throw Object.assign(new ControlError(error?.code ?? "failed", error?.message ?? "the command failed", error?.hint ?? ""), Object.keys(rest).length ? { data: rest } : {});
}

// --- Output ---------------------------------------------------------------------------------------------------------

const scalar = (v) => v === null || ["string", "number", "boolean"].includes(typeof v);

function textLines(value, indent = "") {
  const out = [];
  for (const [k, v] of Object.entries(value)) {
    if (scalar(v)) out.push(`${indent}${k}: ${v}`);
    else if (Array.isArray(v) && v.every(scalar)) out.push(`${indent}${k}: ${v.join(", ") || "(none)"}`);
    else if (Array.isArray(v)) { out.push(`${indent}${k}:`); for (const x of v) out.push(scalar(x) ? `${indent}  - ${x}` : `${indent}  - ${Object.entries(x).map(([a, b]) => `${a}=${scalar(b) ? b : JSON.stringify(b)}`).join(" ")}`); }
    else { out.push(`${indent}${k}:`); out.push(...textLines(v, `${indent}  `)); }
  }
  return out;
}

/** --text: what a person reads. Each command's main output first (the tree, the checks), then the rest as key: value. */
export function renderText(result) {
  const { ok: _ok, command, ...rest } = result;
  const lines = [];
  if (!result.ok && command !== "doctor") {
    const { error, ...more } = rest;
    return [`error (${error.code}): ${error.message}${error.hint ? `\n  what to do: ${error.hint}` : ""}`, ...(Object.keys(more).length ? ["", renderText({ ok: true, command, ...more })] : [])].join("\n");
  }
  if (command === "doctor") {
    for (const c of rest.checks) lines.push(`${c.ok ? "ok  " : "FAIL"} ${c.name}: ${c.detail}${c.fix ? `\n       fix: ${c.fix}` : ""}`);
    lines.push(rest.healthy ? "doctor: ready to start a session" : "doctor: fix the FAIL lines, then run it again");
    return lines.join("\n");
  }
  if (command === "snapshot") {
    const { tree, ...more } = rest;
    return [tree, "", ...textLines(more)].join("\n");
  }
  if (command === "flow" && rest.steps) {
    for (const s of rest.steps) lines.push(`${s.ok ? "ok  " : "FAIL"} ${String(s.n).padStart(2)} ${s.step}${s.ms !== undefined ? ` (${s.ms} ms)` : ""}${s.error ? `\n       ${s.error}` : ""}`);
    const { steps: _steps, ...more } = rest;
    return [...lines, ...textLines(more)].join("\n");
  }
  return textLines(rest).join("\n") || "ok";
}

// --- Main -----------------------------------------------------------------------------------------------------------

/** Runs one command line; returns { result, exit, help? } (help is text, printed as it is). */
export async function run(argv) {
  let parsed;
  try { parsed = parseArgs(argv); } catch (e) {
    // Name the command when it was one (`goto` without its route), so the caller knows which line failed.
    const named = argv.find((a) => !a.startsWith("-"));
    return { result: errorResult(named in COMMANDS ? named : null, e), exit: e.exit ?? 1, text: argv.includes("--text") };
  }
  const { command, args, options, text, help } = parsed;
  if (command !== null && help) return { help: commandHelp(command), exit: 0 };
  if (command === null || (command === "help" && !options.markdown && !args[0])) return { help: overviewHelp(), exit: 0 };
  if (command === "help") {
    if (options.markdown) return { help: markdownReference().trimEnd(), exit: 0 };
    if (!(args[0] in COMMANDS)) return { result: errorResult("help", usage(`unknown command "${args[0]}"`, `\`${PROG} --help\` lists them`)), exit: 2, text };
    return { help: commandHelp(args[0]), exit: 0 };
  }
  try {
    let data;
    if (command === "doctor") {
      const checks = await doctorChecks();
      data = { healthy: checks.every((c) => c.ok), checks };
      return { result: { ok: data.healthy, command, ...data, ...(data.healthy ? {} : { error: { code: "unhealthy", message: "a check failed", hint: checks.filter((c) => !c.ok).map((c) => c.fix).join("; ") } }) }, exit: data.healthy ? 0 : 1, text };
    }
    if (command === "start") data = await start(options);
    else if (command === "stop") data = await stop(options);
    else if (command === "cleanup") data = await cleanup(options);
    else if (command === "flows") data = await listFlows();
    else {
      const state = requireSession();
      if (command === "reset" && options["dry-run"]) data = resetPlan(state);
      else {
        const target = command === "screenshot" ? screenshotTarget(args[0]) : undefined;
        data = replyData(await rpc(state, { command, args, options, ...(target ? { path: target } : {}) }, command === "flow" ? 600000 : 180000));
      }
    }
    return { result: { ok: true, command, ...data }, exit: 0, text };
  } catch (e) {
    return { result: errorResult(command, e), exit: e instanceof ControlError ? e.exit : 1, text };
  }
}

async function main() {
  const out = await run(process.argv.slice(2));
  if (out.help) process.stdout.write(`${out.help}\n`);
  else process.stdout.write(`${out.text ? renderText(out.result) : JSON.stringify(out.result)}\n`);
  process.exitCode = out.exit;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main();
