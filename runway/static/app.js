"use strict";

// ------------------------------------------------------------------------------------------ helpers
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const money0 = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const fmt = (n) => money.format(n ?? 0);
const fmt0 = (n) => money0.format(n ?? 0);
const parseDate = (s) => { const [y, m, d] = s.slice(0, 10).split("-").map(Number); return new Date(y, m - 1, d); };
// Dates never break across lines ("Oct" at the end of one line, "30" on the next): spaces become non-breaking.
const fmtDate = (s, opts = { month: "short", day: "numeric" }) => parseDate(s).toLocaleDateString("en-US", opts).replace(/ /g, "\u00a0");
// Keep a short phrase (an account name, "balance $3,969.12") on one line.
const nw = (html) => `<span class="nw">${html}</span>`;
// An account's institution logo (from runway/static/banks), or its first letter when there's no logo for it.
function acctIcon(id) {
  const b = (STATE.brands || {})[id];
  if (!b) return "";
  const title = esc(b.institution || "");
  return b.logo ? `<img class="bank-icon" src="/banks/${esc(b.logo)}.svg" alt="" title="${title}" width="18" height="18" loading="lazy">`
    : `<span class="bank-icon letter" title="${title}" aria-hidden="true">${esc(b.initial)}</span>`;
}
const acctLabel = (id, name) => `<span class="acct">${acctIcon(id)}<span>${esc(name || "")}</span></span>`;
const fmtDow = (s) => fmtDate(s, { weekday: "short", month: "short", day: "numeric" });
// "tomorrow", "Monday" (within a week) or "Oct 12"
const relDay = (s, today) => {
  const days = Math.round((parseDate(s) - parseDate(today)) / 864e5);
  if (days === 0) return "today";
  if (days === 1) return "tomorrow";
  if (days > 1 && days < 7) return parseDate(s).toLocaleDateString("en-US", { weekday: "long" });
  return fmtDate(s);
};

async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: {} };
  if (init.method !== "GET") init.headers["X-Runway"] = "1";
  if (opts.body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opts.body); }
  const res = await fetch(path, init);
  if (res.status === 401) {   // signed out (session expired): go sign in, then come back here
    location.href = "/auth/login?next=" + encodeURIComponent("/" + location.hash);
    throw new Error("Signing you in again…");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

let toastTimer;
function toast(msg, isError = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = isError ? "error" : "";
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), isError ? 6000 : 3000);
}

let CATEGORIES = [];
// Fill in path/depth/top from the parent links if the server didn't send them (e.g. an older server still running).
function withPaths(list) {
  const parentOf = Object.fromEntries(list.map((c) => [c.name, c.parent || null]));
  for (const c of list) {
    if (Array.isArray(c.path) && c.path.length) continue;
    const path = [c.name];
    let p = parentOf[c.name];
    while (p && !path.includes(p) && path.length < 10) { path.unshift(p); p = parentOf[p]; }
    c.path = path; c.depth = path.length - 1; c.top = path[0];
  }
  return list;
}
async function loadCategories() { CATEGORIES = withPaths(await api("/api/categories")); }
const CAT_MAX_DEPTH = 2;  // levels including the top one (matches the server): Parent > Sub
const monthLabel = (m) => { const [y, mo] = m.split("-").map(Number); return new Date(y, mo - 1, 1).toLocaleDateString("en-US", { month: "long", year: "numeric" }); };
const catParentOf = (name) => (CATEGORIES.find((c) => c.name === name) || {}).parent || null;
const catLabel = (c) => (c.path && c.path.length > 1 ? c.path.join(" > ") : c.name);
function categoryOptions(selected, { blank = true, canHoldChildren = false, exclude = null } = {}) {
  // CATEGORIES comes back in tree order: each category followed by its subcategories, at any depth.
  const groups = [["Spending", (c) => !c.is_transfer && !c.is_income], ["Money in", (c) => c.is_income], ["Not spending", (c) => c.is_transfer]];
  let html = blank ? `<option value="">Choose…</option>` : "";
  for (const [label, test] of groups) {
    const items = CATEGORIES.filter(test)
      .filter((c) => !canHoldChildren || (c.depth || 0) < CAT_MAX_DEPTH - 1)
      .filter((c) => !exclude || !exclude(c));
    if (!items.length) continue;
    html += `<optgroup label="${label}">` + items.map((c) =>
      `<option value="${esc(c.name)}" ${c.name === selected ? "selected" : ""}>${esc(catLabel(c))}</option>`).join("") + `</optgroup>`;
  }
  return html;
}

// ------------------------------------------------------------------------------------------ state / header
let STATE = {};
async function refreshState() {
  STATE = await api("/api/state");
  $$("#review-badge, .review-count").forEach((b) => { b.hidden = !STATE.review_count; b.textContent = STATE.review_count || ""; });
  showSyncStatus();
  const ver = $("#brand-ver");   // the running version, next to the name (hover for the full build)
  if (ver) { ver.textContent = STATE.version && STATE.version !== "dev" ? STATE.version : "dev"; ver.title = `Runway ${ver.textContent}`; ver.hidden = false; }
  const u = STATE.user;
  if (u && !u.local) {
    const name = u.name || u.email || "Signed in";
    $("#account-name").textContent = name; $("#account-name").title = u.email || ""; $("#account-name").hidden = false;
    $("#avatar").textContent = name.split(/[\s@.]+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("");
    $("#avatar").hidden = false; $("#sign-out").hidden = false;
  }
}

// Sync runs on its own (daily, and when you open Runway), so the sidebar just says how fresh the data is.
function showSyncStatus() {
  const s = $("#sync-status"), dot = $("#sync-dot");
  dot.className = "sync-dot";
  if (STATE.syncing) { s.textContent = "Syncing…"; dot.classList.add("busy"); }
  else if (STATE.last_log && !STATE.last_log.ok) { s.textContent = "Last sync failed"; s.title = STATE.last_log.message || ""; dot.classList.add("bad"); }
  else if (STATE.last_sync_ok) {
    const t = new Date(STATE.last_sync_ok), today = new Date().toDateString() === t.toDateString();
    s.textContent = "Up to date · " + (today ? t.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })
      : t.toLocaleDateString("en-US", { month: "short", day: "numeric" }));
    s.title = "Last synced " + t.toLocaleString();
  } else { s.textContent = STATE.connected ? "Not synced yet" : "Bank not connected"; dot.classList.add(STATE.connected ? "busy" : "bad"); }
}

// ------------------------------------------------------------------------------------------ router
const PAGES = { overview: renderOverview, budget: renderBudget, reports: renderReports, investments: renderInvestments, networth: renderNetWorth, review: renderReview, transactions: renderTransactions,
  recurring: renderRecurring, setup: renderSetup };
async function route() {
  let [page, sub] = (location.hash || "#overview").slice(1).split("?")[0].split("/");
  if (page === "settings") page = "setup";
  if (!PAGES[page]) page = "overview";
  const navPage = page === "review" ? "transactions" : page;   // Review is a tab of Transactions
  $$("[data-page]").forEach((a) => a.classList.toggle("active", a.dataset.page === navPage));
  const on = $(`.nav a[data-page="${navPage}"]`);
  if (on && window.innerWidth <= 860) on.scrollIntoView({ block: "nearest", inline: "center" });
  try { await PAGES[page]($("#app"), sub); } catch (err) { console.error(err); $("#app").innerHTML = `<div class="card">Something went wrong: ${esc(err.message)}</div>`; }
}
window.addEventListener("hashchange", route);
// Charts are drawn to fit their box, so redraw the page when the window width changes enough to matter
// (but never while you're typing in a field).
let lastWidth = window.innerWidth, resizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    if (Math.abs(window.innerWidth - lastWidth) < 60) return;
    const f = document.activeElement;
    if (f && (f.tagName === "INPUT" || f.tagName === "TEXTAREA" || f.tagName === "SELECT")) return;
    if (document.querySelector(".cost-row")) return;   // an editor is open
    lastWidth = window.innerWidth;
    route();
  }, 300);
});

// Autosave: run `save` whenever a field's value changes (text fields when you leave them or press Enter),
// then flash a small "Saved" mark on the field.
function onEdit(fields, save) {
  for (const f of fields) {
    if (!f) continue;
    let last = f.type === "checkbox" ? f.checked : f.value;
    const run = async () => {
      const now = f.type === "checkbox" ? f.checked : f.value;
      if (now === last) return;
      try { await save(f); last = now; markSaved(f); }
      catch (err) { toast(err.message, true); }
    };
    f.addEventListener("change", run);
    if (f.tagName === "INPUT" && f.type !== "checkbox") f.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); f.blur(); } });
  }
}
function markSaved(f) {
  const host = f.closest("label") || f;
  host.classList.remove("just-saved"); void host.offsetWidth; host.classList.add("just-saved");
  setTimeout(() => host.classList.remove("just-saved"), 1600);
}

// ------------------------------------------------------------------------------------------ overview
let horizon = null;
async function renderOverview(el) {
  if (!STATE.connected) {
    el.innerHTML = `<div class="card empty"><h2>Connect your bank to get started</h2>
      <p>Link SimpleFIN or Plaid and Runway projects where your cash is headed.</p>
      <a class="btn primary" href="#setup/connections">Go to Settings</a></div>`;
    return;
  }
  horizon = horizon || STATE.horizon_days || 90;
  const fc = await api(`/api/overview?days=${horizon}`);
  const cashNow = fc.accounts.reduce((s, a) => s + a.balance, 0);
  const low = fc.low;
  const lowBad = low && low.balance < 0;
  const end = fc.total.length ? fc.total[fc.total.length - 1] : cashNow;
  const lastDate = fc.dates[fc.dates.length - 1];
  const allCards = fc.cards.concat(fc.unlinked_cards || []);   // cards not linked yet still count toward what's owed
  const owed = allCards.reduce((s, c) => s + (c.owed_now || 0), 0);
  const nextDue = fc.cards.filter((c) => c.remaining > 0 && c.due_date >= fc.today).sort((x, y) => x.due_date.localeCompare(y.due_date))[0];
  const allChecking = fc.accounts.length && fc.accounts.every((a) => a.kind === "checking");
  const what = fc.accounts.length === 1 ? (allChecking ? "Checking" : esc(fc.accounts[0].name)) : "Your cash";
  const span = horizon === 180 ? "6 months" : `${horizon} days`;

  // The headline: where the balance bottoms out, and why.
  let headline = "", lede = "";
  if (low && fc.accounts.length) {
    const lowEvents = fc.events.filter((e) => e.date === low.date && e.amount < 0).sort((x, y) => x.amount - y.amount);
    const nextIn = fc.events.find((e) => e.amount > 0 && e.date > low.date);
    const when = low.date === fc.today ? "today" : relDay(low.date, fc.today);
    const nb = (t) => t.replace(/ /g, "\u00a0");   // keep "Dec 21" and "90 days" on one line
    headline = lowBad
      ? `Heads up. ${what} dips to <span class="hl-bad">${fmt0(low.balance)}</span> ${nb(when === "today" ? "today" : "on " + when)}.`
      : `You’re on track. ${what} stays above <span class="hl">${fmt0(low.balance)}</span> for the next ${nb(span)}.`;
    lede = [
      low.date === fc.today ? "Today is the tightest point in the forecast."
        : `The tightest moment is ${when}${lowEvents.length ? `, when ${lowEvents[0].kind === "card" ? `the ${nw(esc(lowEvents[0].name.replace(/ statement$/, "")))} payment` : esc(lowEvents[0].name)} goes out` : ""}.`,
      nextIn ? `Next money in: ${nw(esc(nextIn.name) + ",")} ${fmt0(nextIn.amount)} on ${nw(relDay(nextIn.date, fc.today))}.` : "",
    ].filter(Boolean).join(" ");
  }

  let html = "";
  for (const w of fc.warnings) html += `<div class="warn"><span class="icon">!</span><span>${esc(w)} <a href="#setup/${/Plaid/.test(w) ? "connections" : "accounts"}">Settings</a></span></div>`;
  for (const m of fc.missed || []) html += missedLine(m);
  if (!fc.accounts.length) {
    html += `<div class="warn"><span class="icon">!</span><span>No account to forecast yet. Choose your primary checking account in <a href="#setup/accounts">Settings</a>.</span></div>`;
  }

  html += `<section class="hero">
    <div class="hero-text">
      <span class="eyebrow">${parseDate(fc.today).toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" })}</span>
      ${headline ? `<h1 class="headline">${headline}</h1>` : `<h1 class="headline">Overview</h1>`}
      ${lede ? `<p class="lede">${lede}</p>` : ""}
    </div>
    <div class="hero-balance">
      <span class="muted small">${allChecking ? "In checking today" : "Cash today"}</span>
      <span class="big-num">${fmt(cashNow)}</span>
      <span class="muted small">${fc.accounts.map((a) => esc(a.name)).join(" + ") || "—"}</span>
    </div>
  </section>`;

  html += `<div class="tiles">
    <div class="tile tile-row ${lowBad ? "alert" : ""}"><div><div class="label">${lowBad ? "Goes negative" : "Lowest point"}</div><div class="sub">${low ? fmtDow(low.date) : ""}</div></div>
      <div class="value low-val">${low ? fmt(low.balance) : "—"}</div></div>
    <div class="tile tile-row"><div><div class="label">In ${span}</div><div class="sub ${end - cashNow >= 0 ? "pos" : "neg"}">${end - cashNow >= 0 ? "+" : "−"}${fmt(Math.abs(end - cashNow))}</div></div>
      <div class="value">${fmt(end)}</div></div>
    <div class="tile tile-row"><div><div class="label">Owed on cards</div><div class="sub">${allCards.length} card${allCards.length === 1 ? "" : "s"}${nextDue ? ` · next due ${fmtDate(nextDue.due_date)}` : ""}</div></div>
      <div class="value">${fmt(owed)}</div></div>
  </div>`;

  html += `<div class="card">
    <div class="card-head"><h2>The next ${span}${fc.accounts.length === 1 && !allChecking ? ` · ${esc(fc.accounts[0].name)}` : ""}</h2>
      <div class="seg" id="horizon" role="group" aria-label="Forecast length">${[...new Set([30, 60, 90, 180, horizon])].sort((x, y) => x - y).map((d) => `<button data-d="${d}" class="${d === horizon ? "on" : ""}" aria-pressed="${d === horizon}">${d === 180 ? "6 months" : d + " days"}</button>`).join("")}</div></div>
    ${fc.budget ? `<div class="chart-legend"><span><i class="lg-line"></i>Forecast</span><span title="Spends your budgets (${fmt0(fc.budget.monthly)} a month) on each budget's card, in place of estimated card statements.${budgetSkipped(fc.budget.skipped)}"><i class="lg-line lg-budget"></i>If you stick to your budget
      · low ${fmt0(fc.budget.low.balance)} on ${fmtDate(fc.budget.low.date)}</span></div>` : ""}
    <div class="chart-wrap" id="chart"></div>
    ${fc.accounts.length > 1 ? `<p class="help" style="margin-top:12px">${fc.accounts.length} accounts combined · <a href="#setup/accounts">pick a primary account</a></p>` : ""}
    <details><summary class="small muted">Show as table</summary>${weeklyTable(fc)}</details>
  </div>`;

  html += `<div class="grid-2">
    <div class="card"><div class="card-head"><h2>Coming up</h2></div>${eventsTable(fc.events)}</div>
    <div class="card"><div class="card-head"><h2>Credit cards</h2></div><div class="scroll-x">${cardsTable(fc.cards)}</div></div>
  </div>`;

  el.innerHTML = html;
  wireMissed(el);
  $$("#horizon button").forEach((b) => b.addEventListener("click", () => { horizon = Number(b.dataset.d); renderOverview(el); }));
  drawChart($("#chart"), fc);
  wireEvents(el);
  wireCards(el);
  $("#show-all-events")?.addEventListener("click", () => { showAllEvents = true; renderOverview(el); });
}

// Click an upcoming amount to change just that one occurrence.
function wireEvents(root) {
  $$(".ev-amt:not(.stmt-amt)", root).forEach((b) => b.addEventListener("click", () => {
    const td = b.closest("td, .ev-amount");
    const cur = Number(b.dataset.amount);
    td.innerHTML = `<input type="number" step="0.01" class="ev-input" value="${Math.abs(cur).toFixed(2)}" style="width:110px" aria-label="Amount">`;
    const input = $("input", td);
    input.focus(); input.select();
    let done = false;
    const finish = async (save) => {
      if (done) return; done = true;
      if (save && input.value !== "" && Number(input.value) !== Math.abs(cur)) {
        const amount = (cur < 0 ? -1 : 1) * Math.abs(Number(input.value));
        try { await api("/api/overrides", { method: "POST", body: { key: b.dataset.key, amount } }); toast("Updated for this date only"); }
        catch (err) { toast(err.message, true); }
      }
      route();
    };
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") finish(true); if (e.key === "Escape") finish(false); });
    input.addEventListener("blur", () => finish(true));
  }));
  $$(".ev-reset", root).forEach((b) => b.addEventListener("click", async () => {
    try { await api("/api/overrides", { method: "DELETE", body: { key: b.dataset.key } }); toast("Back to the usual amount"); route(); }
    catch (err) { toast(err.message, true); }
  }));
}

let showAllEvents = false;
function eventsTable(events) {
  if (!events.length) return `<div class="empty">Nothing scheduled. Add paychecks and bills on the <a href="#recurring">Recurring</a> tab.</div>`;
  const shown = showAllEvents ? events : events.slice(0, 8);
  return `<div class="ev-list">${shown.map((e) => {
    const d = parseDate(e.date);
    const sub = [e.kind === "card" ? "Card payment" : esc(e.category || (e.kind === "recurring" ? "Recurring" : "")),
      e.balance_after < 0 ? `<span class="neg-bal">balance ${fmt(e.balance_after)}</span>` : `balance ${fmt(e.balance_after)}`].filter(Boolean).map(nw).join(" · ");
    return `<div class="ev-row">
      <div class="ev-date"><span>${d.toLocaleDateString("en-US", { month: "short" })}</span><b>${d.getDate()}</b></div>
      <div class="ev-main"><span class="ev-name">${e.kind === "recurring" ? `<span class="rec-icon" title="Recurring item">↻</span>` : ""}${esc(e.name)}${e.estimated ? `<span class="tag" title="${e.kind === "card" ? "Statement hasn't closed yet; based on the card's average over its last 3 statements" : "Based on recent payments"}">estimate</span>` : ""}${e.overridden ? `<span class="tag edited" title="Usually ${fmt(e.original_amount)}">edited</span>` : ""}</span>
        <span class="ev-sub">${sub}</span></div>
      <div class="ev-amount">${e.key ? `<button class="ev-amt ${e.amount > 0 ? "pos" : ""}" data-key="${esc(e.key)}" data-amount="${e.amount}" title="Change this amount for this date only">${e.amount > 0 ? "+" : "−"}${fmt(Math.abs(e.amount))}</button>` : fmt(e.amount)}
        ${e.overridden ? `<button class="btn link ev-reset" data-key="${esc(e.key)}" title="Go back to the usual amount">reset</button>` : ""}</div>
    </div>`; }).join("")}</div>
    ${events.length > shown.length ? `<button class="btn link" id="show-all-events" style="margin-top:6px">Show all ${events.length}</button>` : ""}`;
}

function cardsTable(cards) {
  if (!cards.length) return `<div class="empty">Link your cards through Plaid in <a href="#setup/connections">Settings → Connections</a> to see their statements and due dates.</div>`;
  return `<table id="cards-table"><tr><th>Card</th><th class="num">Statement</th><th class="num">Due</th>
      <th class="num" title="Average spending per statement over the last 3 statements; used to forecast future payments">Avg / stmt</th></tr>
    ${cards.map((c) => {
      const soon = c.remaining > 0 && (parseDate(c.due_date) - parseDate(new Date().toISOString().slice(0, 10))) / 864e5 <= 7;
      return `<tr><td><div class="card-name">${acctLabel(c.id, c.name)}</div><div class="cell-sub">owes ${fmt(c.owed_now)} now</div></td>
      <td class="num"><button class="ev-amt stmt-amt" data-key="${esc(c.statement_key)}" data-amount="${c.statement_balance}"
          title="Closed ${fmtDate(c.last_close)} · click to correct it">${fmt(c.statement_balance)}</button>
        ${c.statement_set ? `<span class="tag edited" title="Entered by you · the bank reported ${fmt(c.statement_reported)}">set</span>` : ""}
        <div class="cell-sub">${c.remaining > 0 ? (c.remaining < c.statement_balance - 0.005 ? `${fmt(c.remaining)} left` : "unpaid") : `<span class="pos">Paid ✓</span>`}${
          c.remaining > 0 && c.minimum_payment ? ` · ${nw(`min ${fmt(c.minimum_payment)}`)}` : ""}${c.statement_set
          ? ` · <button class="btn link stmt-reset" data-key="${esc(c.statement_key)}" title="Go back to the bank's figure (${fmt(c.statement_reported)})">reset</button>` : ""}</div></td>
      <td class="num ${soon ? "due-soon" : "muted"}">${fmtDate(c.due_date)}</td>
      <td class="num muted" title="${c.avg_cycles ? `From the last ${c.avg_cycles} statement${c.avg_cycles === 1 ? "" : "s"}` : "Not enough history yet; using recent daily spending"}">${c.avg_monthly_spend != null ? fmt(c.avg_monthly_spend) : "—"}</td></tr>`; }).join("")}</table>
    `;
}

function wireCards(root) {
  $$(".stmt-amt", root).forEach((b) => b.addEventListener("click", () => {
    const td = b.closest("td");
    const cur = Number(b.dataset.amount);
    td.innerHTML = `<input type="number" step="0.01" min="0" class="ev-input" value="${cur.toFixed(2)}" style="width:110px" aria-label="Statement balance">`;
    const input = $("input", td);
    input.focus(); input.select();
    let done = false;
    const finish = async (save) => {
      if (done) return; done = true;
      if (save && input.value !== "" && Number(input.value) !== cur) {
        try { await api("/api/overrides", { method: "POST", body: { key: b.dataset.key, amount: Math.abs(Number(input.value)) } }); toast("Statement balance saved"); }
        catch (err) { toast(err.message, true); }
      }
      route();
    };
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") finish(true); if (e.key === "Escape") finish(false); });
    input.addEventListener("blur", () => finish(true));
  }));
  $$(".stmt-reset", root).forEach((b) => b.addEventListener("click", async () => {
    try { await api("/api/overrides", { method: "DELETE", body: { key: b.dataset.key } }); toast("Back to the calculated amount"); route(); }
    catch (err) { toast(err.message, true); }
  }));
}

// "Left out: Mortgage and Utilities, which recurring items already cover; Medical, whose account isn't in the forecast."
function budgetSkipped(skipped) {
  if (!skipped.length) return "";
  const list = (xs) => xs.length < 3 ? xs.join(" and ") : xs.slice(0, -1).join(", ") + " and " + xs[xs.length - 1];
  const by = {};
  for (const k of skipped) (by[k.reason] ||= []).push(esc(k.category));
  const parts = Object.entries(by).map(([reason, cats]) => {
    const why = reason.startsWith("a recurring") ? (cats.length > 1 ? "which recurring items already cover" : "which a recurring item already covers")
      : reason.startsWith("its account") ? (cats.length > 1 ? "whose accounts aren't in the forecast" : "whose account isn't in the forecast") : esc(reason);
    return `${list(cats)}, ${why}`;
  });
  return ` Left out: ${parts.join("; ")}.`;
}

function weeklyTable(fc) {
  const rows = [];
  for (let i = 0; i < fc.dates.length; i += 7) rows.push(`<tr><td>${fmtDow(fc.dates[i])}</td><td class="num">${fmt(fc.total[i])}</td></tr>`);
  return `<table style="max-width:360px"><tr><th>Date</th><th class="num">Projected balance</th></tr>${rows.join("")}</table>`;
}

// ------------------------------------------------------------------------------------------ chart
function niceTicks(min, max, count = 5) {
  const span = max - min || Math.abs(max) || 1;
  const raw = span / count;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw);
  const start = Math.floor(min / step) * step, end = Math.ceil(max / step) * step;
  const ticks = [];
  for (let v = start; v <= end + step / 2; v += step) ticks.push(Math.round(v * 100) / 100);
  return ticks;
}
function shortMoney(v) {
  const a = Math.abs(v);
  const s = a >= 1e6 ? (a / 1e6).toFixed(1).replace(/\.0$/, "") + "M" : a >= 1e3 ? (a / 1e3).toFixed(a >= 1e4 ? 0 : 1).replace(/\.0$/, "") + "k" : a.toFixed(0);
  return (v < 0 ? "−$" : "$") + s;
}

function drawChart(host, fc) {
  const series = fc.total;
  if (!series.length) { host.innerHTML = `<div class="empty">No cash accounts in the forecast yet.</div>`; return; }
  const W = Math.max(320, host.clientWidth), H = 280;
  const m = { top: 30, right: 8, bottom: 28, left: 52 };
  const iw = W - m.left - m.right, ih = H - m.top - m.bottom;
  const alt = fc.budget && fc.budget.total && fc.budget.total.length === series.length ? fc.budget.total : null;
  const both = alt ? series.concat(alt) : series;
  let lo = Math.min(...both), hi = Math.max(...both);
  if (lo > 0 && lo < hi * 0.25) lo = 0;  // near zero: show the floor
  const ticks = niceTicks(lo, hi);
  const y0 = ticks[0], y1 = ticks[ticks.length - 1];
  const x = (i) => m.left + (i / (series.length - 1)) * iw;
  const y = (v) => m.top + (1 - (v - y0) / (y1 - y0 || 1)) * ih;

  const eventsByDate = {};
  for (const e of fc.events) (eventsByDate[e.date] ||= []).push(e);

  const pts = series.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`);
  const baseY = y(Math.max(y0, Math.min(0, y1)) === 0 ? 0 : y0);
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Projected balance over the next ${series.length - 1} days">`;
  svg += `<g class="grid">${ticks.map((t) => `<line x1="${m.left}" x2="${W - m.right}" y1="${y(t)}" y2="${y(t)}"/>`).join("")}</g>`;
  svg += `<g class="axis">${ticks.map((t) => `<text x="${m.left - 8}" y="${y(t) + 4}" text-anchor="end">${shortMoney(t)}</text>`).join("")}</g>`;
  // month labels on the x axis
  let xl = "";
  fc.dates.forEach((d, i) => {
    const dt = parseDate(d);
    if (i === 0 || dt.getDate() === 1) {
      const label = i === 0 ? "Today" : dt.toLocaleDateString("en-US", { month: "short" });
      if (i === 0 || x(i) - m.left > 56) xl += `<text x="${x(i)}" y="${H - 8}" text-anchor="${i === 0 ? "start" : "middle"}">${label}</text>`;
    }
  });
  svg += `<g class="axis">${xl}</g>`;
  if (y0 < 0 && y1 > 0) svg += `<line class="zero" x1="${m.left}" x2="${W - m.right}" y1="${y(0)}" y2="${y(0)}"/>`;
  // event ticks along the baseline
  fc.dates.forEach((d, i) => { if (eventsByDate[d]) svg += `<line class="event-tick" x1="${x(i)}" x2="${x(i)}" y1="${m.top + ih}" y2="${m.top + ih + 5}"/>`; });
  svg += `<path class="area" d="M${pts[0]} L${pts.join(" L")} L${x(series.length - 1)},${y(y0)} L${x(0)},${y(y0)} Z"/>`;
  if (alt) svg += `<path class="line budget-line" d="M${alt.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" L")}"/>`;
  svg += `<path class="line" d="M${pts.join(" L")}"/>`;
  // From the first estimated card payment on, the line includes spending that hasn't happened yet.
  const firstEst = fc.events.filter((e) => e.kind === "card" && e.estimated).map((e) => e.date).sort()[0];
  const ei = firstEst ? fc.dates.indexOf(firstEst) : -1;
  if (ei > 0) {
    const ex0 = x(ei), right = ex0 < W - m.right - 150;
    svg += `<line class="est-start" x1="${ex0}" x2="${ex0}" y1="${m.top - 6}" y2="${m.top + ih}"/>`;
    svg += `<text class="est-start-label" x="${ex0 + (right ? 6 : -6)}" y="${m.top - 10}" text-anchor="${right ? "start" : "end"}">${right ? "Estimated new spending from here →" : "← Estimated new spending from here"}</text>`;
  }
  // low point
  const li = fc.dates.indexOf(fc.low.date);
  if (li >= 0) {
    const lx = x(li), ly = y(series[li]);
    const anchor = lx > W - 140 ? "end" : lx < m.left + 80 ? "start" : "middle";
    svg += `<circle class="low-dot" cx="${lx}" cy="${ly}" r="5.5"/>`;
    svg += `<text class="low-label" x="${lx + (anchor === "start" ? 10 : anchor === "end" ? -10 : 0)}" y="${ly + (ly > m.top + ih - 30 ? -14 : 24)}" text-anchor="${anchor}">Low ${fmt0(series[li])} · ${fmtDate(fc.low.date)}</text>`;
  }
  // where it ends up
  const ex = x(series.length - 1), ey = y(series[series.length - 1]);
  svg += `<text class="end-note" x="${ex}" y="${ey < m.top + 20 ? ey + 18 : ey - 10}" text-anchor="end">${fmt0(series[series.length - 1])} by ${fmtDate(fc.dates[fc.dates.length - 1])}</text>`;
  svg += `<g id="hover" style="display:none"><line class="cross" y1="${m.top}" y2="${m.top + ih}"/><circle class="hover-dot" r="5"/></g>`;
  svg += `<rect id="hit" x="${m.left}" y="${m.top}" width="${iw}" height="${ih}" fill="transparent"/>`;
  svg += `</svg><div class="tooltip" hidden></div>`;
  host.innerHTML = svg;
  // a small plate behind the low-point label so the line doesn't run through it
  const ll = $(".low-label", host), en = $(".end-note", host);
  const plate = (el, cls) => { const b = el.getBBox(); el.insertAdjacentHTML("beforebegin", `<rect class="${cls}" x="${b.x - 6}" y="${b.y - 3}" width="${b.width + 12}" height="${b.height + 6}" rx="5"/>`); };
  if (ll) {
    const bb = ll.getBBox();
    ll.insertAdjacentHTML("beforebegin", `<rect class="low-label-bg" x="${bb.x - 7}" y="${bb.y - 4}" width="${bb.width + 14}" height="${bb.height + 8}" rx="6"/>`);
    // Keep the "ends at" note clear of the low-point label: move it above (or below) the label, or drop it when
    // the low point is the end anyway.
    if (en && fc.dates.indexOf(fc.low.date) >= series.length - 4) en.remove();   // the low point is the end
    else if (en) {
      const lo = { x: bb.x - 7, y: bb.y - 4, w: bb.width + 14, h: bb.height + 8 };
      const overlaps = (b) => b.x < lo.x + lo.w + 4 && b.x + b.width > lo.x - 4 && b.y < lo.y + lo.h + 4 && b.y + b.height > lo.y - 4;
      if (overlaps(en.getBBox())) {
        en.setAttribute("y", lo.y - 8 > m.top + 10 ? lo.y - 8 : lo.y + lo.h + 16);
        if (overlaps(en.getBBox())) en.remove();
      }
    }
  }

  if (en && en.isConnected) plate(en, "end-note-bg");   // so the line doesn't run through "$1,631 by Dec 24"

  const hover = $("#hover", host), tip = $(".tooltip", host), hit = $("#hit", host), svgEl = $("svg", host);
  const move = (clientX) => {
    const r = svgEl.getBoundingClientRect();
    const px = ((clientX - r.left) / r.width) * W;
    const i = Math.max(0, Math.min(series.length - 1, Math.round(((px - m.left) / iw) * (series.length - 1))));
    const cx = x(i), cy = y(series[i]);
    hover.style.display = "";
    $("line", hover).setAttribute("x1", cx); $("line", hover).setAttribute("x2", cx);
    $("circle", hover).setAttribute("cx", cx); $("circle", hover).setAttribute("cy", cy);
    const evs = eventsByDate[fc.dates[i]] || [];
    tip.innerHTML = `<div class="tt-date">${fmtDow(fc.dates[i])}</div><div class="tt-val">${fmt(series[i])}</div>` +
      (alt ? `<div class="tt-ev tt-budget"><span>If you stick to your budget</span><span>${fmt(alt[i])}</span></div>` : "") +
      evs.map((e) => `<div class="tt-ev"><span>${esc(e.name)}${e.estimated ? " (est.)" : ""}</span><span>${fmt(e.amount)}</span></div>`).join("");
    tip.hidden = false;
    const sx = (cx / W) * r.width, tw = tip.offsetWidth;
    tip.style.left = Math.min(Math.max(0, sx + 12), r.width - tw) + "px";
    tip.style.top = Math.max(0, (cy / H) * r.height - 60) + "px";
  };
  hit.addEventListener("mousemove", (e) => move(e.clientX));
  hit.addEventListener("touchmove", (e) => { move(e.touches[0].clientX); e.preventDefault(); }, { passive: false });
  hit.addEventListener("mouseleave", () => { hover.style.display = "none"; tip.hidden = true; });
}

// ------------------------------------------------------------------------------------------ review + transactions
// Both pages share one list: same filters, same columns. Changing a category saves immediately.
let upcomingAll = false;
const LIST_STATE = {
  review: { q: "", account: "", category: "", month: "", scope: "", remember: true },
  transactions: { q: "", account: "", category: "", month: "", scope: "", remember: false },
};
function renderReview(el) { return renderTxPage(el, "review"); }
function renderTransactions(el) { return renderTxPage(el, "transactions"); }

async function renderTxPage(el, mode) {
  const f = LIST_STATE[mode];
  const review = mode === "review";
  await loadCategories();
  const [accounts, recurringItems] = await Promise.all([api("/api/accounts"), api("/api/recurring")]);
  el.innerHTML = `<div class="card-head"><h1>Transactions <span class="muted small" id="tx-count"></span></h1>
      ${review ? `<button class="btn primary" id="ai-suggest" ${STATE.has_api_key ? "" : "disabled title=\"Add an OpenRouter key in Settings → Connections first\""}>Suggest categories with AI</button>` : ""}</div>
    <div class="subtabs" role="tablist"><a href="#transactions" role="tab" class="${review ? "" : "active"}">All</a>
      <a href="#review" role="tab" class="${review ? "active" : ""}">To review <span class="badge review-count" ${STATE.review_count ? "" : "hidden"}>${STATE.review_count || ""}</span></a></div>
    ${review ? `
      <div id="ai-panel"></div>
      ${STATE.has_api_key ? `<details class="card ai-log" id="ai-log" ${aiLogOpen ? "open" : ""}><summary><b>AI activity</b> <span class="muted small" id="ai-log-sum"></span></summary>
        <div id="ai-log-body" class="small muted">Loading…</div></details>` : ""}` : ""}
    <div class="toolbar">
      <input type="search" id="tx-q" placeholder="Search merchant or description" value="${esc(f.q)}">
      <select id="tx-account"><option value="">All accounts</option>${accounts.map((a) =>
        `<option value="${esc(a.id)}" ${a.id === f.account ? "selected" : ""}>${esc(a.display_name || a.name)}</option>`).join("")}</select>
      <select id="tx-category"><option value="">All categories</option><option value="__none__" ${f.category === "__none__" ? "selected" : ""}>Uncategorized</option>
        ${CATEGORIES.map((c) => `<option value="${esc(c.name)}" ${c.name === f.category ? "selected" : ""}>${esc(catLabel(c))}</option>`).join("")}</select>
      ${f.month ? `<span class="filter-chip">${esc(monthLabel(f.month))}${f.scope === "budget" ? " · accounts counted in Budget" : ""}
        <button class="chip-x" id="tx-month-clear" aria-label="Show all dates">✕</button></span>` : ""}
      <label class="inline" title="When you pick a category, also save a rule so future transactions from this merchant get it automatically">
        <input type="checkbox" id="tx-remember" ${f.remember ? "checked" : ""}> Remember for this merchant</label>
    </div>
    ${review ? "" : `<div id="tx-upcoming"></div>`}
    <div id="bulk-bar" class="bulk-bar" hidden></div>
    <div class="card scroll-x" id="tx-list"><div class="empty">Loading…</div></div>`;

  // Upcoming (projected) items for the forecast account, filtered the same way as the list below.
  const upcoming = review ? null : api(`/api/overview?days=${STATE.horizon_days || 90}`).then((fc) => fc.events).catch(() => []);
  const showUpcoming = async () => {
    const box = $("#tx-upcoming");
    if (!box) return;
    const q = f.q.trim().toLowerCase();
    const events = (await upcoming).filter((e) =>
      (!q || e.name.toLowerCase().includes(q)) && (!f.account || e.account_id === f.account) &&
      (!f.category || (f.category === "__none__" ? !e.category : e.category === f.category || catParentOf(e.category) === f.category)) &&
      (!f.month || e.date.startsWith(f.month)));
    if (!events.length) { box.innerHTML = ""; return; }
    const shown = upcomingAll ? events : events.slice(0, 6);
    box.innerHTML = `<div class="card scroll-x projected"><div class="card-head"><h2>Upcoming <span class="tag">projected</span></h2></div>
      <table><tr><th>Date</th><th>Item</th><th class="hide-sm">Account</th><th class="num">Amount</th><th>Category</th><th class="num">Balance after</th></tr>
      ${shown.map((e) => `<tr>
        <td class="muted" style="white-space:nowrap">${fmtDow(e.date)}</td>
        <td>${e.kind === "recurring" ? `<span class="rec-icon" title="Recurring item">↻</span>` : ""}${esc(e.name)}${e.estimated ? `<span class="tag">estimate</span>` : ""}${e.overridden ? `<span class="tag edited" title="Usually ${fmt(e.original_amount)}">edited</span>` : ""}</td>
        <td class="muted hide-sm">${acctLabel(e.account_id, e.account)}</td>
        <td class="num"><button class="ev-amt ${e.amount > 0 ? "pos" : ""}" data-key="${esc(e.key)}" data-amount="${e.amount}" title="Change this amount for this date only">${e.amount > 0 ? "+" : ""}${fmt(e.amount)}</button>
          ${e.overridden ? `<button class="btn link ev-reset" data-key="${esc(e.key)}">reset</button>` : ""}</td>
        <td class="muted">${esc(e.category || "—")}</td>
        <td class="num ${e.balance_after < 0 ? "neg-bal" : "muted"}">${e.balance_after < 0 ? "▲ " : ""}${fmt(e.balance_after)}</td></tr>`).join("")}</table>
      ${events.length > shown.length ? `<button class="btn link" id="up-all">Show all ${events.length} upcoming</button>` : ""}</div>`;
    wireEvents(box);
    $("#up-all", box)?.addEventListener("click", () => { upcomingAll = true; showUpcoming(); });
  };

  const load = async () => {
    showUpcoming();
    const qs = new URLSearchParams({ q: f.q, account: f.account, category: f.category, month: f.month, scope: f.scope, limit: "300" });
    if (review) qs.set("review", "1");
    const data = await api(`/api/transactions?${qs}`);
    const box = $("#tx-list");
    if (!box || !$("#tx-count")) return;  // you've moved to another page meanwhile
    const filtered = f.q || f.account || f.category || f.month;
    $("#tx-count").textContent = review ? (data.total ? `${data.total} to go` : "") : `${data.total}`;
    const bulkBar = $("#bulk-bar");
    if (bulkBar) { bulkBar.hidden = true; bulkBar.innerHTML = ""; }
    if (!data.items.length) {
      box.innerHTML = `<div class="empty">${review && !filtered ? "All caught up. New transactions that need a decision will show up here." : "No transactions match."}</div>`;
      return;
    }
    box.innerHTML = `<table><tr><th class="sel"><input type="checkbox" id="tx-sel-all" aria-label="Select all shown"></th><th>Date</th><th>Merchant</th><th class="hide-sm">Account</th><th class="num">Amount</th><th>Category</th></tr>
      ${data.items.map((t) => txRow(t, review)).join("")}
    </table>${data.total > data.items.length ? `<p class="help">Showing ${data.items.length} of ${data.total}. Narrow the search to see more.</p>` : ""}`;
    const byId = Object.fromEntries(data.items.map((t) => [t.id, t]));
    wireBulk(box, byId, load);
    $$("tr[data-id]", box).forEach((tr) => {
      $("select.cat", tr)?.addEventListener("change", (e) => { if (e.target.value) save(tr, e.target.value); });
      $(".keep", tr)?.addEventListener("click", () => save(tr, $("select.cat", tr).value));
      $(".rec-btn", tr).addEventListener("click", () => openRecurringPicker(tr, recurringItems, load));
      $(".split-btn", tr).addEventListener("click", () => openSplitEditor(tr, byId[tr.dataset.id], load));
      $(".order-tag", tr)?.addEventListener("click", () => openOrderRow(tr, byId[tr.dataset.id].retail.order_id, load));
    });
  };

  const save = async (tr, category) => {
    const sel = $("select.cat", tr);
    sel.disabled = true;
    try {
      const r = await api(`/api/transactions/${encodeURIComponent(tr.dataset.id)}/category`, {
        method: "POST", body: { category, remember: f.remember },
      });
      toast(r.also_updated ? `Saved · ${r.also_updated} more from this merchant updated too` : "Saved");
      refreshState();
      if (r.also_updated) return load();
      if (review) {
        tr.remove();
        if (!$$("#tx-list tr[data-id]").length) return load();
        const cnt = $("#tx-count"), n = cnt ? parseInt(cnt.textContent, 10) : 0;
        if (n > 0) cnt.textContent = `${n - 1} to go`;
      } else {
        $(".tag.review", tr)?.remove();
        $(".tag.ai", tr)?.remove();
        $(".keep", tr)?.remove();
        sel.disabled = false;
      }
    } catch (err) {
      toast(err.message, true);
      sel.disabled = false;
    }
  };

  let timer;
  $("#tx-q").addEventListener("input", (e) => { f.q = e.target.value; clearTimeout(timer); timer = setTimeout(load, 250); });
  $("#tx-account").addEventListener("change", (e) => { f.account = e.target.value; load(); });
  $("#tx-category").addEventListener("change", (e) => { f.category = e.target.value; load(); });
  $("#tx-month-clear")?.addEventListener("click", () => { f.month = ""; f.scope = ""; route(); });
  $("#tx-remember").addEventListener("change", (e) => { f.remember = e.target.checked; });
  $("#ai-suggest")?.addEventListener("click", (e) => runAiSuggestions(e.currentTarget, f, load));
  $("#ai-log")?.addEventListener("toggle", (e) => { aiLogOpen = e.currentTarget.open; });
  loadAiLog();
  load();
}

// The merchant's logo when Plaid has one (Runway serves it; nothing is fetched from elsewhere), else its initial.
function merchantIcon(t) {
  const name = (t.payee || t.description || "?").replace(/^[^A-Za-z0-9]+/, "");
  return t.logo ? `<img class="m-logo" src="${esc(t.logo)}" alt="" loading="lazy" width="20" height="20">`
    : `<span class="m-logo m-initial" aria-hidden="true">${esc((name[0] || "?").toUpperCase())}</span>`;
}

function txRow(t, review) {
  const suggestion = t.needs_review && t.category && t.category_source === "ai";
  const linked = t.recurring_id > 0;
  const split = t.is_split && (t.splits || []).length;
  return `<tr data-id="${esc(t.id)}" data-account="${esc(t.account_id)}" ${split ? 'class="has-split"' : ""}>
    <td class="sel"><input type="checkbox" class="tx-sel" aria-label="Select this transaction"></td>
    <td class="muted" style="white-space:nowrap">${fmtDate(t.posted)}${t.pending ? `<span class="tag">pending</span>` : ""}</td>
    <td><div class="merchant">${merchantIcon(t)}${esc(t.payee || t.description)}
        <button class="rec-btn ${linked ? "linked" : ""}" title="${linked ? `Recurring: ${esc(t.recurring_name)} (click to change)` : "Link to a recurring item"}">↻${linked ? `<span class="rec-name">${esc(t.recurring_name)}</span>` : ""}</button>
        ${t.retail ? `<button class="tag order-tag" title="See what was in this ${t.retail.retailer === "amazon" ? "Amazon" : "Target"} order">${orderLabel(t.retail)}</button>` : ""}</div>
      <div class="desc" title="${esc(t.description)}">${esc(t.description)}</div>
      <div class="desc show-sm">${acctLabel(t.account_id, t.account_name)}</div></td>
    <td class="muted hide-sm">${acctLabel(t.account_id, t.account_name)}</td>
    <td class="num ${t.amount > 0 ? "pos" : ""}">${fmt(t.amount)}</td>
    <td style="white-space:nowrap">${split
      ? `<span class="tag split">split</span><span class="split-parts">${t.splits.map((s) =>
          `<span ${s.note ? `title="${esc(s.note)}"` : ""}>${esc(s.category)} ${fmt(Math.abs(s.amount))}</span>`).join(" · ")}</span>`
      : `<select class="cat ${review ? "" : "ghost"}" aria-label="Category">${categoryOptions(t.category)}</select>`}
      ${suggestion ? `<span class="tag ai" title="AI suggestion confidence">${Math.round((t.confidence || 0) * 100)}%</span>
        <button class="btn link keep" title="Keep the suggested category">✓ Keep</button>` : ""}
      ${!review && t.needs_review ? `<span class="tag review">review</span>` : ""}
      <button class="btn link split-btn" title="Spread this across several categories">${split ? "Edit split" : "Split"}</button></td></tr>`;
}

// Tick transactions (shift-click for a range) to change them together: a category, the merchant's name, or
// marking them reviewed.
function wireBulk(box, byId, reload) {
  const boxes = $$(".tx-sel", box);
  const bar = $("#bulk-bar");   // outside the list's card, so it can stay in view while you scroll
  bar.innerHTML = "";
  bar.hidden = true;
  let last = null;
  const chosen = () => boxes.filter((b) => b.checked).map((b) => b.closest("tr").dataset.id);
  const draw = () => {
    const ids = chosen();
    boxes.forEach((b) => b.closest("tr").classList.toggle("selected", b.checked));
    $("#tx-sel-all", box).checked = ids.length > 0 && ids.length === boxes.length;
    $("#tx-sel-all", box).indeterminate = ids.length > 0 && ids.length < boxes.length;
    bar.hidden = !ids.length;
    if (!ids.length) return;
    const total = ids.reduce((n, id) => n + (byId[id]?.amount || 0), 0);
    if (!bar.firstChild) {
      bar.innerHTML = `<span class="bulk-count"></span>
        <select id="bulk-cat" aria-label="Category for the selected transactions"><option value="">Set category…</option>${categoryOptions("", { blank: false })}</select>
        <span class="bulk-rename"><input id="bulk-payee" placeholder="Rename merchant to…" aria-label="New merchant name"><button class="btn" id="bulk-payee-go">Rename</button></span>
        <button class="btn" id="bulk-reviewed" title="Keep their categories and take them out of Review">Mark reviewed</button>
        <button class="btn link" id="bulk-clear">Clear</button>`;
      const send = async (body, what) => {
        const ids = chosen();
        try {
          const r = await api("/api/transactions/bulk", { method: "POST", body: { ids, ...body } });
          toast(`${what} · ${r.updated} transaction${r.updated === 1 ? "" : "s"}`);
          refreshState(); reload();
        } catch (err) { toast(err.message, true); }
      };
      $("#bulk-cat", bar).addEventListener("change", (e) => { if (e.target.value) send({ category: e.target.value }, `Set to ${e.target.value}`); });
      const rename = () => { const v = $("#bulk-payee", bar).value.trim(); if (v) send({ payee: v }, `Renamed to ${v}`); };
      $("#bulk-payee-go", bar).addEventListener("click", rename);
      $("#bulk-payee", bar).addEventListener("keydown", (e) => { if (e.key === "Enter") rename(); });
      $("#bulk-reviewed", bar).addEventListener("click", () => send({ reviewed: true }, "Marked reviewed"));
      $("#bulk-clear", bar).addEventListener("click", () => { boxes.forEach((b) => { b.checked = false; }); draw(); });
    }
    $(".bulk-count", bar).innerHTML = `<b>${ids.length} selected</b> <span class="muted">${fmt(total)}</span>`;
  };
  boxes.forEach((b, i) => b.addEventListener("click", (e) => {
    if (e.shiftKey && last !== null) {   // shift-click: everything between the last tick and this one
      const [a, z] = [Math.min(last, i), Math.max(last, i)];
      for (let k = a; k <= z; k++) boxes[k].checked = b.checked;
    }
    last = i;
    draw();
  }));
  $("#tx-sel-all", box).addEventListener("change", (e) => { boxes.forEach((b) => { b.checked = e.target.checked; }); draw(); });
}

// Spread one transaction across categories: each part gets its own category and amount, and they must add up.
// Amounts are typed as plain numbers; the transaction's own sign (a charge or a deposit) is kept.
function openSplitEditor(tr, t, reload) {
  if (tr.nextElementSibling?.classList.contains("split-edit")) return;
  const sign = t.amount < 0 ? -1 : 1;
  const total = Math.abs(t.amount);
  const existing = (t.splits || []).map((s) => ({ category: s.category, amount: Math.abs(s.amount).toFixed(2), note: s.note }));
  const parts = existing.length ? existing : [{ category: t.category || "", amount: total.toFixed(2) }, { category: "", amount: "" }];
  const row = document.createElement("tr");
  row.className = "split-edit";
  row.innerHTML = `<td colspan="6"><div class="split-box">
    <div class="split-head"><b>Split ${fmt(total)}</b> <span class="muted small">${esc(t.payee || t.description)}</span></div>
    <div class="split-rows"></div>
    <div class="split-foot">
      <button class="btn link split-add">+ Add a part</button>
      <span class="split-left muted"></span>
      <span class="split-actions">
        ${existing.length ? `<button class="btn link split-remove">Remove split</button>` : ""}
        <button class="btn split-cancel">Cancel</button>
        <button class="btn primary split-save">Save split</button></span></div></div></td>`;
  tr.after(row);

  const rows = $(".split-rows", row);
  const addRow = (part) => {
    const line = document.createElement("div");
    line.className = "split-row";
    line.innerHTML = `<select class="split-cat" aria-label="Category">${categoryOptions(part.category)}</select>
      <input class="split-amt num" type="number" step="0.01" min="0" inputmode="decimal" aria-label="Amount" value="${esc(part.amount)}">
      <input class="split-note" placeholder="Note (optional)" value="${esc(part.note || "")}">
      <button class="btn link split-drop" title="Remove this part" aria-label="Remove this part">✕</button>`;
    rows.append(line);
    $(".split-amt", line).addEventListener("input", left);
    $(".split-drop", line).addEventListener("click", () => { line.remove(); left(); });
  };
  const typed = () => $$(".split-row", rows).map((line) => ({
    category: $(".split-cat", line).value,
    amount: parseFloat($(".split-amt", line).value),
    note: $(".split-note", line).value,
  }));
  function left() {
    const sum = typed().reduce((n, p) => n + (isFinite(p.amount) ? p.amount : 0), 0);
    const rest = Math.round((total - sum) * 100) / 100;
    $(".split-left", row).textContent = rest ? `${fmt(Math.abs(rest))} ${rest > 0 ? "left to assign" : "over"}` : "adds up";
    $(".split-left", row).classList.toggle("over", rest < 0);
  }
  parts.forEach(addRow);
  left();

  $(".split-add", row).addEventListener("click", () => {
    const sum = typed().reduce((n, p) => n + (isFinite(p.amount) ? p.amount : 0), 0);
    const rest = Math.round((total - sum) * 100) / 100;
    addRow({ category: "", amount: rest > 0 ? rest.toFixed(2) : "" });
    left();
  });
  $(".split-cancel", row).addEventListener("click", () => row.remove());
  const send = async (body, msg) => {
    try {
      await api(`/api/transactions/${encodeURIComponent(t.id)}/split`, { method: "POST", body });
      toast(msg);
      refreshState();
      reload();
    } catch (err) { toast(err.message, true); }
  };
  $(".split-remove", row)?.addEventListener("click", () => send({ splits: [] }, "Split removed"));
  $(".split-save", row).addEventListener("click", () => {
    const parts = typed();
    if (parts.some((p) => !p.category)) return toast("Give every part a category", true);
    if (parts.some((p) => !isFinite(p.amount) || p.amount <= 0)) return toast("Give every part an amount", true);
    send({ splits: parts.map((p) => ({ category: p.category, amount: sign * p.amount, note: p.note })) }, "Split saved");
  });
  $(".split-cat", rows)?.focus();
}

// ------------------------------------------------------------------------------------------------ Amazon and Target orders

const STORES = { amazon: "Amazon", target: "Target" };
const orderLabel = (o) => `${STORES[o.retailer] || o.retailer}${o.channel === "store" ? " in store" : ""}${o.items ? ` · ${o.items} item${o.items === 1 ? "" : "s"}` : ""}`;
const ITEM_SOURCES = { manual: "you picked", memory: "as before", ai: "AI", department: "store's department" };

// An order under its transaction (or in Settings): its items, each with a category you can change (remembered for
// the next time you buy it), and the card charges it was paid with.
function openOrderRow(tr, orderId, reload) {
  const next = tr.nextElementSibling;
  if (next?.classList.contains("order-edit")) { next.remove(); return; }
  const row = document.createElement("tr");
  row.className = "order-edit";
  row.innerHTML = `<td colspan="6"><div class="order-box">Loading…</div></td>`;
  tr.after(row);
  renderOrder($(".order-box", row), orderId, () => { reload?.(); });
}

async function renderOrder(box, orderId, changed) {
  let o;
  try { o = await api(`/api/retail/orders/${encodeURIComponent(orderId)}`); }
  catch (err) { box.innerHTML = `<div class="muted">${esc(err.message)}</div>`; return; }
  const store = STORES[o.retailer] || o.retailer;
  const totals = [o.subtotal != null ? `items ${fmt(o.subtotal)}` : "", o.shipping ? `shipping ${fmt(o.shipping)}` : "",
    o.tax != null ? `tax ${fmt(o.tax)}` : ""].filter(Boolean).join(" · ");
  box.innerHTML = `<div class="order-head"><b>${esc(store)} ${o.channel === "store" ? "purchase" : "order"} ${esc(o.order_number)}</b>
      <span class="muted small">${o.placed ? fmtDate(o.placed, { month: "short", day: "numeric", year: "numeric" }) : ""}
        ${o.total != null ? ` · ${fmt(o.total)}` : ""}${totals ? ` (${totals})` : ""}${o.payment ? ` · ${esc(o.payment)}` : ""}</span>
      <a class="btn link" href="${esc(o.url)}" target="_blank" rel="noopener">Open on ${esc(o.retailer === "amazon" ? "amazon.com" : "target.com")}</a></div>
    ${o.items.length ? `<div class="order-items">${o.items.map((i) => `<div class="order-item" data-id="${i.id}">
        <span class="order-title" title="${esc(i.title)}">${i.quantity > 1 ? `<span class="muted">${i.quantity}×</span> ` : ""}${esc(i.title)}</span>
        <span class="num muted">${fmt(i.amount)}</span>
        <select class="item-cat ghost" aria-label="Category for ${esc(i.title)}">${categoryOptions(i.category)}</select>
        <span class="muted small item-src">${i.category ? esc(ITEM_SOURCES[i.category_source] || "") : "uses the transaction's category"}</span></div>`).join("")}</div>
      <p class="help small">A category you pick here is used for this item in every order, now and next time.</p>`
    : `<p class="muted">${o.details ? "No items in this order." : "Runway hasn't read this order's items yet; they come with the next import."}</p>`}
    <div class="order-charges">${o.charges.map((c) => `<div class="order-charge" data-id="${esc(c.id)}">
        <span class="muted">${c.amount > 0 ? "Refund" : "Charged"} ${fmtDate(c.date)} · ${fmt(Math.abs(c.amount))}${c.payment ? ` · ${esc(c.payment)}` : ""}</span>
        ${c.tx_id ? `<span>→ ${esc(c.payee || c.description || "")} ${fmtDate(c.posted)} <span class="muted">${esc(c.account_name || "")}</span>
            ${c.applied ? `<span class="tag">${c.applied === "split" ? "split by items" : "categorized by items"}</span>` : ""}</span>
          <span class="order-actions">${c.amount < 0 && !c.applied && o.items.length ? `<button class="btn link ch-apply" title="Replace the category you set with the order's items">Split by items</button>` : ""}
            <button class="btn link ch-unlink" title="This charge isn't that transaction">Not this transaction</button></span>`
        : `<span class="muted">not matched to a transaction</span><span class="order-actions"><button class="btn link ch-pick">Pick one…</button></span>`}
      </div>`).join("") || `<p class="muted small">No card charges for this order yet.</p>`}</div>`;

  const redo = () => { changed?.(); renderOrder(box, orderId, changed); };
  $$(".item-cat", box).forEach((sel) => sel.addEventListener("change", async () => {
    if (!sel.value) return;
    try {
      const r = await api(`/api/retail/items/${sel.closest("[data-id]").dataset.id}`, { method: "POST", body: { category: sel.value } });
      toast(r.orders > 1 ? `Saved · used in ${r.orders} orders` : "Saved");
      redo();
    } catch (err) { toast(err.message, true); }
  }));
  $$(".order-charge", box).forEach((line) => {
    const id = encodeURIComponent(line.dataset.id);
    $(".ch-unlink", line)?.addEventListener("click", async () => {
      try { await api(`/api/retail/charges/${id}/unlink`, { method: "POST" }); toast("Unmatched, and the transaction is back as it was"); redo(); }
      catch (err) { toast(err.message, true); }
    });
    $(".ch-apply", line)?.addEventListener("click", async () => {
      try { await api(`/api/retail/charges/${id}/apply`, { method: "POST" }); toast("Split by items"); redo(); }
      catch (err) { toast(err.message, true); }
    });
    $(".ch-pick", line)?.addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      const list = await api(`/api/retail/charges/${id}/candidates`).catch(() => []);
      const pick = document.createElement("div");
      pick.className = "order-pick";
      pick.innerHTML = list.length ? list.map((t) => `<button class="btn link" data-tx="${esc(t.id)}">${fmtDate(t.posted)} · ${esc(t.payee || t.description)} · ${fmt(t.amount)}
          <span class="muted">${esc(t.account_name)}</span></button>`).join("") : `<span class="muted small">No transactions near that date and amount.</span>`;
      line.after(pick);
      $$("[data-tx]", pick).forEach((b) => b.addEventListener("click", async () => {
        try { await api(`/api/retail/charges/${id}/link`, { method: "POST", body: { tx_id: b.dataset.tx } }); toast("Matched"); redo(); }
        catch (err) { toast(err.message, true); }
      }));
    });
  });
}

async function renderRetailCard(box) {
  let r;
  try { r = await api("/api/retail"); } catch (err) { box.innerHTML = `<h2>Amazon and Target orders</h2><p class="muted">${esc(err.message)}</p>`; return; }
  const storeLine = (k) => {
    const s = r.stores[k];
    if (!s.last && !s.orders) return `<div class="tidy-row retail-row"><span class="tidy-main"><b>${esc(s.name)}</b></span><span class="muted small">not imported yet</span></div>`;
    return `<div class="tidy-row retail-row"><span class="tidy-main"><b>${esc(s.name)}</b></span>
      <span class="muted small">${s.orders} order${s.orders === 1 ? "" : "s"} · ${s.matched} charge${s.matched === 1 ? "" : "s"} matched
        ${s.unmatched ? ` · <span class="warn-text" title="Charges with no transaction: paid with a card that isn't in Runway, or not posted yet">${s.unmatched} not matched</span>` : ""}
        ${s.last ? ` · imported ${esc(relTime(s.last))}` : ""}</span></div>`;
  };
  box.innerHTML = `<h2>Amazon and Target orders <span class="muted small">optional, via the Runway browser extension</span></h2>
    <p class="help">Runway matches each Amazon or Target charge to its order (online, or in store with your Target account) and
      splits the transaction by what you bought. Neither store has an API for this, so a small extension in your browser reads your
      orders with the sign-in you already have there and sends them only to Runway.</p>
    <ol class="help steps">
      <li><a href="/api/retail/extension.zip" download>Download the extension</a>, unzip it, and in Chrome (or Edge, Brave, Arc) open
        <code>chrome://extensions</code>, turn on Developer mode and choose <b>Load unpacked</b>.</li>
      <li>Give it Runway's address (${esc(location.origin)}) and a key:
        ${r.token ? `<span class="muted">key made ${esc(relTime(r.token_created))}</span> <button class="btn link" id="rt-new">Make a new key</button>
          <button class="btn link" id="rt-remove">Remove</button>` : `<button class="btn" id="rt-new">Make a key</button>`}
        <div id="rt-shown"></div></li>
      <li>Stay signed in to Amazon and Target in that browser, and use the extension's <b>Import</b> button.</li>
    </ol>
    <div class="tidy-list">${storeLine("amazon")}${storeLine("target")}</div>
    <div class="form-row" style="margin-top:10px">
      ${STATE.has_api_key ? `<label class="inline"><input type="checkbox" id="rt-ai" ${r.ai ? "checked" : ""}> Categorize items with AI
        <span class="muted small">(only item names and prices are sent)</span></label>`
        : `<span class="help small">Add an OpenRouter key below and Runway can categorize each item for you; until then items take the transaction's category until you pick one.</span>`}
      ${r.recent.length ? `<button class="btn" id="rt-match">Match and split again</button>` : ""}
    </div>
    ${r.recent.length ? `<details class="retail-recent"><summary class="small">Recent orders</summary>
      <div class="tidy-list">${r.recent.map((o) => `<div class="retail-order">
        <button class="order-open" data-id="${esc(o.id)}">
          <span class="muted small">${o.placed ? fmtDate(o.placed) : ""}</span>
          <span class="order-name">${esc(orderLabel(o))} <span class="muted small">${esc(o.order_number)}</span></span>
          <span class="num">${o.total != null ? fmt(o.total) : ""}</span>
          <span class="small ${o.charges && o.matched === o.charges ? "muted" : "warn-text"}">${!o.details ? "items not read"
            : !o.charges ? "no charge" : o.matched === o.charges ? "matched" : `${o.charges - o.matched} not matched`}</span></button>
        <div class="order-box" hidden></div></div>`).join("")}</div></details>` : ""}`;

  const again = () => renderRetailCard(box);
  $("#rt-new", box)?.addEventListener("click", async (e) => {
    if (r.token && !confirmInline(e.currentTarget, "Replace the key? The extension will need the new one")) return;
    try {
      const { token } = await api("/api/retail/token", { method: "POST" });
      await again();
      $("#rt-shown", box).innerHTML = `<div class="form-row"><input id="rt-key" readonly value="${esc(token)}" style="width:340px">
        <button class="btn" id="rt-copy">Copy</button><span class="muted small">Shown once: paste it into the extension's options now.</span></div>`;
      $("#rt-copy", box).addEventListener("click", () => { navigator.clipboard?.writeText(token).then(() => toast("Copied"), () => {}); $("#rt-key", box).select(); });
      $("#rt-key", box).select();
    } catch (err) { toast(err.message, true); }
  });
  $("#rt-remove", box)?.addEventListener("click", async (e) => {
    if (!confirmInline(e.currentTarget, "Remove? The extension stops working")) return;
    await api("/api/retail/token/remove", { method: "POST" }); toast("Key removed"); again();
  });
  $("#rt-ai", box)?.addEventListener("change", async (e) => {
    await api("/api/retail/settings", { method: "POST", body: { ai: e.target.checked } }); toast("Saved");
  });
  $("#rt-match", box)?.addEventListener("click", async (e) => {
    const b = e.currentTarget; b.disabled = true; b.textContent = "Working…";
    try {
      const out = await api("/api/retail/match", { method: "POST" });
      toast(`${out.matched} newly matched · ${out.split} split · ${out.category} categorized`);
      refreshState(); again();
    } catch (err) { toast(err.message, true); b.disabled = false; b.textContent = "Match and split again"; }
  });
  $$(".order-open", box).forEach((b) => b.addEventListener("click", () => {
    const panel = b.nextElementSibling;
    panel.hidden = !panel.hidden;
    if (!panel.hidden) { panel.innerHTML = "Loading…"; renderOrder(panel, b.dataset.id, null); }
  }));
}

function relTime(iso) {
  if (!iso) return "";
  const d = new Date(iso.includes("T") ? iso : iso.replace(" ", "T") + "Z");
  const s = (Date.now() - d) / 1000;
  if (s < 90) return "just now";
  if (s < 5400) return `${Math.round(s / 60)} minutes ago`;
  if (s < 129600) return `${Math.round(s / 3600)} hours ago`;
  return fmtDate(d.toISOString().slice(0, 10), { month: "short", day: "numeric", year: "numeric" });
}

// Link a transaction to a recurring item, start a new one from it, or mark it as not recurring.
function openRecurringPicker(tr, items, reload) {
  const btn = $(".rec-btn", tr);
  const acct = tr.dataset.account;
  const same = items.filter((r) => r.account_id === acct), other = items.filter((r) => r.account_id !== acct);
  const opt = (r) => `<option value="${r.id}">${esc(r.name)} · ${esc(r.frequency)}</option>`;
  const sel = document.createElement("select");
  sel.className = "rec-picker";
  sel.innerHTML = `<option value="">Recurring…</option>
    ${same.length ? `<optgroup label="Link to">${same.map(opt).join("")}</optgroup>` : ""}
    ${other.length ? `<optgroup label="Other accounts">${other.map(opt).join("")}</optgroup>` : ""}
    <optgroup label="New recurring item from this"><option value="new:monthly">Monthly</option><option value="new:biweekly">Every 2 weeks</option>
      <option value="new:weekly">Weekly</option><option value="new:yearly">Yearly</option></optgroup>
    ${btn.classList.contains("linked") ? `<option value="none">Not recurring</option>` : ""}`;
  btn.replaceWith(sel);
  sel.focus();
  sel.addEventListener("blur", () => { if (!sel.dataset.busy) reload(); });
  sel.addEventListener("change", async () => {
    if (!sel.value) return;
    sel.dataset.busy = "1";
    const body = sel.value.startsWith("new:") ? { new: sel.value.slice(4) } : sel.value === "none" ? { recurring_id: null } : { recurring_id: Number(sel.value) };
    try {
      await api(`/api/transactions/${encodeURIComponent(tr.dataset.id)}/recurring`, { method: "POST", body });
      toast(body.new ? "Recurring item created; edit it on the Recurring tab" : body.recurring_id ? "Linked" : "Marked as not recurring");
    } catch (err) { toast(err.message, true); }
    reload();
  });
}

let aiLogOpen = false;
async function loadAiLog() {
  const body = $("#ai-log-body");
  if (!body) return;
  let rows;
  try { rows = await api("/api/ai/log"); } catch (err) { body.textContent = err.message; return; }
  if (!$("#ai-log-body")) return;
  const last = rows[0];
  $("#ai-log-sum").textContent = last
    ? `· last ${last.purpose === "review" ? "run" : last.purpose === "orders" ? "order items run" : "automatic run"} ${new Date(last.at.replace(" ", "T")).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}: ${last.ok ? `${last.answered} of ${last.merchants} suggested` : "failed"}`
    : "· nothing yet";
  body.innerHTML = rows.length ? `<table class="ai-log-table"><tr><th>When</th><th>What</th><th>Model</th><th class="num">Result</th><th class="num">Time</th></tr>
    ${rows.map((r) => `<tr class="${r.ok ? "" : "ai-fail"}">
      <td style="white-space:nowrap">${esc(new Date(r.at.replace(" ", "T")).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", second: "2-digit" }))}</td>
      <td>${r.purpose === "review" ? "Suggest button" : r.purpose === "orders" ? "Amazon and Target items" : "Automatic, during sync"}<div class="desc ai-msg">${r.ok ? "" : "▲ "}${esc(r.message || "")}</div>
        ${r.reply ? `<details class="ai-reply"><summary>What the model said</summary><pre>${esc(r.reply)}</pre></details>` : ""}</td>
      <td><code>${esc(r.model || "")}</code></td>
      <td class="num">${r.ok ? `${r.answered}/${r.merchants}` : "error"}</td>
      <td class="num">${r.seconds != null ? `${r.seconds}s` : ""}</td></tr>`).join("")}</table>`
    : `<p>No AI requests yet. Click “Suggest categories with AI” and each request will show up here.</p>`;
}

async function runAiSuggestions(btn, f, reload) {
  const panel = $("#ai-panel");
  btn.disabled = true; btn.textContent = "Asking the AI…";
  panel.innerHTML = `<div class="card empty">Asking the AI about each merchant in Review… <span id="ai-wait"></span></div>`;
  const began = Date.now();
  const tick = setInterval(() => { const w = $("#ai-wait"); if (!w) return clearInterval(tick); w.textContent = `${Math.round((Date.now() - began) / 1000)}s`; }, 1000);
  let groups;
  try { groups = await api("/api/ai/suggest", { method: "POST" }); }
  catch (err) {
    panel.innerHTML = `<div class="warn critical"><span class="icon">!</span><span>${esc(err.message)}</span></div>`;
    btn.disabled = false; btn.textContent = "Suggest categories with AI";
    const log = $("#ai-log"); if (log) { log.open = true; aiLogOpen = true; } loadAiLog(); return;
  }
  loadAiLog();
  clearInterval(tick);
  btn.disabled = false; btn.textContent = "Ask again";
  if (!groups.length) { panel.innerHTML = `<div class="card empty">Nothing waiting for a category.</div>`; return; }
  const answered = groups.filter((g) => g.category || g.new_category).length;
  panel.innerHTML = `<div class="card ai-card"><div class="card-head"><h2>AI suggestions · ${groups.length} merchant${groups.length === 1 ? "" : "s"}</h2>
      <span class="small muted">Nothing changes until you apply</span></div>
    <p class="help">${answered === groups.length ? `The AI suggested a category for every merchant.`
      : answered ? `The AI suggested a category for ${answered} of ${groups.length}; pick the rest yourself.`
      : `The AI didn't suggest anything this time. Try again, or switch to a stronger model in Settings → Connections (for example anthropic/claude-haiku-4.5).`}</p>
    <table>${groups.map((g, i) => `<tr data-i="${i}">
      <td><div class="merchant">${esc(g.merchant)}${g.direction === "in" ? `<span class="tag">money in</span>` : ""}</div>
        <div class="desc">${g.count} transaction${g.count === 1 ? "" : "s"} · ${fmt(g.total)}</div>
        ${g.examples.map((x) => `<div class="desc" title="${esc(x)}">${esc(x)}</div>`).join("")}</td>
      <td style="white-space:nowrap"><select class="ai-cat" aria-label="Category">${g.new_category
          ? `<option value="__new__" selected>✦ New: ${esc(g.new_category.name)}${g.new_category.parent ? ` (in ${esc(g.new_category.parent)})` : ""}</option>` : ""}${categoryOptions(g.new_category ? "" : g.category)}</select>
        ${g.new_category ? `<span class="tag ai" title="Nothing existing fit, so the AI suggests adding this category. Applying creates it.">new category · ${Math.round(g.confidence * 100)}%</span>`
          : g.category ? `<span class="tag ai" title="AI confidence">${Math.round(g.confidence * 100)}%</span>` : `<span class="tag">no suggestion</span>`}</td>
      <td style="white-space:nowrap" class="num"><button class="btn primary ai-apply">Apply to ${g.count}</button> <button class="btn ai-skip">Skip</button></td></tr>`).join("")}</table></div>`;
  const done = (tr) => { tr.remove(); if (!$$("#ai-panel tr[data-i]").length && document.body.contains(panel)) panel.innerHTML = ""; };
  $$("#ai-panel tr[data-i]").forEach((tr) => {
    const g = groups[Number(tr.dataset.i)];
    $(".ai-skip", tr).addEventListener("click", () => done(tr));
    $(".ai-apply", tr).addEventListener("click", async (e) => {
      const btn = e.currentTarget, choice = $(".ai-cat", tr).value;
      if (!choice) { toast("Choose a category first", true); return; }
      btn.disabled = true;
      try {
        const body = { tx_ids: g.tx_ids, remember: f.remember, direction: g.direction };
        if (choice === "__new__") body.new_category = g.new_category; else body.category = choice;
        const r = await api("/api/ai/apply", { method: "POST", body });
        if (r.created) await loadCategories();
        toast(`${g.merchant}: ${r.category}${r.created ? " (new category)" : ""} applied to ${r.updated}`);
        done(tr); refreshState(); reload();
      } catch (err) { toast(err.message, true); btn.disabled = false; }
    });
  });
}

// Missed recurring payments: one line each, with "Find it" (Transactions around that date) and "Dismiss".
function missedLine(m) {
  return `<div class="warn missed" data-key="${esc(m.key)}"><span class="icon">!</span><span>
    <b>${esc(m.name)}</b>: ${fmt(Math.abs(m.amount))} ${m.amount > 0 ? "expected in" : "expected"} ${fmtDate(m.date, { month: "short", day: "numeric" })} hasn't shown up in ${esc(m.account_name || "the account")}.
    <a href="#transactions" class="m-find" data-acct="${esc(m.account_id)}" data-month="${esc(m.date.slice(0, 7))}">Find it</a> ·
    <button class="btn link m-dismiss">Dismiss</button></span></div>`;
}
function wireMissed(root) {
  $$(".warn.missed", root).forEach((w) => {
    $(".m-find", w).addEventListener("click", (e) => {
      Object.assign(LIST_STATE.transactions, { q: "", category: "", account: e.currentTarget.dataset.acct, month: e.currentTarget.dataset.month, scope: "" });
      toast("Use ↻ on the payment to link it to this recurring item");
    });
    $(".m-dismiss", w).addEventListener("click", async () => {
      try { await api("/api/recurring/dismiss", { method: "POST", body: { key: w.dataset.key } }); w.remove(); toast("Dismissed"); }
      catch (err) { toast(err.message, true); }
    });
  });
}

// ------------------------------------------------------------------------------------------ recurring
const openRecurring = new Set();
async function renderRecurring(el) {
  const [accounts, items] = await Promise.all([api("/api/accounts"), api("/api/recurring")]);
  const suggestions = STATE.connected ? await api("/api/recurring/suggestions") : [];
  const name = (a) => a.display_name || a.name;
  const acctName = (id) => { const a = accounts.find((x) => x.id === id); return a ? name(a) : "?"; };
  const acctOptions = (sel) => accounts.filter((a) => !a.hidden).map((a) => `<option value="${esc(a.id)}" ${a.id === sel ? "selected" : ""}>${esc(name(a))}</option>`).join("");
  const freqOptions = (sel) => [["monthly", "Monthly"], ["biweekly", "Every 2 weeks"], ["weekly", "Weekly"], ["semimonthly", "Twice a month (set days)"],
    ["quarterly", "Quarterly"], ["semiannual", "Every 6 months"], ["yearly", "Yearly"], ["dates", "Specific dates each year"]]
    .map(([v, l]) => `<option value="${v}" ${v === sel ? "selected" : ""}>${l}</option>`).join("");
  const modeOptions = (sel) => [["fixed", "Fixed amount"], ["last", "Same as last payment"], ["avg3", "Average of last 3"]]
    .map(([v, l]) => `<option value="${v}" ${v === (sel || "fixed") ? "selected" : ""}>${l}</option>`).join("");
  const fields = (r = {}) => `
      <label>Name<input class="r-name" value="${esc(r.name || "")}" placeholder="Paycheck" style="width:170px"></label>
      <label>Account<select class="r-acct">${acctOptions(r.account_id || STATE.primary_account)}</select></label>
      <label>Amount<input class="r-amount" type="number" step="0.01" value="${r.amount ?? ""}" placeholder="-120.00" style="width:110px"></label>
      <label>Forecast amount<select class="r-mode">${modeOptions(r.amount_mode)}</select></label>
      <label>How often<select class="r-freq">${freqOptions(r.frequency || "monthly")}</select></label>
      <label class="r-dates-wrap" ${["dates", "semimonthly"].includes(r.frequency) ? "" : "hidden"}>${r.frequency === "semimonthly" ? "Days of the month" : "Dates each year"}
        <input class="r-dates" value="${esc(r.dates || "")}" placeholder="${r.frequency === "semimonthly" ? "1, 15" : "Apr 15, Oct 15"}" style="width:150px"></label>
      <label>${["dates", "semimonthly"].includes(r.frequency) ? "Starting" : "A date it happens"}<input class="r-date" type="date" value="${esc(r.anchor_date || "")}"></label>
      <label>Merchant text<input class="r-match" value="${esc(r.match || "")}" placeholder="e.g. comed" style="width:150px"></label>`;
  const values = (box) => ({
    name: $(".r-name", box).value, account_id: $(".r-acct", box).value, amount: $(".r-amount", box).value,
    amount_mode: $(".r-mode", box).value, frequency: $(".r-freq", box).value, anchor_date: $(".r-date", box).value,
    match: $(".r-match", box).value, active: $(".r-active", box) ? ($(".r-active", box).checked ? 1 : 0) : 1,
    dates: $(".r-dates", box).value,
  });
  // Show the dates box only for schedules that need it, with the right hint.
  const syncFreq = (box) => {
    const f = $(".r-freq", box).value, wrap = $(".r-dates-wrap", box);
    const needs = f === "dates" || f === "semimonthly";
    wrap.hidden = !needs;
    wrap.firstChild.textContent = f === "semimonthly" ? "Days of the month " : "Dates each year ";
    $(".r-dates", box).placeholder = f === "semimonthly" ? "1, 15" : "Apr 15, Oct 15";
    $(".r-date", box).parentElement.firstChild.textContent = needs ? "Starting" : "A date it happens";
  };

  const FREQ = { monthly: "monthly", biweekly: "every 2 weeks", weekly: "weekly", semimonthly: "twice a month", quarterly: "quarterly",
    semiannual: "every 6 months", yearly: "yearly", dates: "on set dates" };
  const recRow = (r) => {
    const amt = r.expected_amount ?? r.amount;
    const sub = [FREQ[r.frequency] || r.frequency, r.next_date ? `next ${fmtDate(r.next_date)}` : "no upcoming date",
      r.matched_count ? `${r.matched_count} matched` : "", (r.missed || []).length ? `<span class="warn-text">missed a payment</span>` : ""].filter(Boolean);
    return `<details class="acct-row rec-item" data-id="${r.id}" ${openRecurring.has(String(r.id)) ? "open" : ""}>
      <summary>${acctIcon(r.account_id) || `<span class="bank-icon letter">↻</span>`}
        <span class="acct-main"><span class="acct-title">${esc(r.name)}${r.active ? "" : ` <span class="tag">paused</span>`}</span>
          <span class="acct-sub">${sub.map(nw).join(" · ")}</span></span>
        <span class="acct-bal ${amt > 0 ? "pos" : ""}">${amt > 0 ? "+" : "−"}${fmt(Math.abs(amt))}</span>
        <span class="acct-chev" aria-hidden="true">›</span></summary>
      ${(r.missed || []).map((m) => missedLine(m)).join("")}
      <div class="acct-edit">${fields(r)}
        <div class="acct-checks wide"><label class="inline"><input type="checkbox" class="r-active" ${r.active ? "checked" : ""}> Active</label>
          ${r.matched_count ? `<button class="btn link r-show">Show matched transactions</button>` : ""}
          <button class="btn link r-del" style="margin-left:auto">Remove</button></div>
        <div class="r-matches wide"></div></div></details>`;
  };
  const moneyIn = items.filter((r) => (r.expected_amount ?? r.amount) > 0), moneyOut = items.filter((r) => !((r.expected_amount ?? r.amount) > 0));
  const group = (title, list) => list.length ? `<div class="acct-group"><div class="acct-group-title">${title}</div>${list.map(recRow).join("")}</div>` : "";

  el.innerHTML = `<div class="card-head"><h1>Recurring</h1><button class="btn primary" id="r-new-btn">Add</button></div>
    <div class="card acct-form" id="rec-new" ${items.length ? "hidden" : ""}><h2>Add a recurring item</h2><div class="acct-edit" style="padding-left:0">${fields()}</div>
      <div class="form-row"><button class="btn primary" id="r-add">Add</button>${items.length ? `<button class="btn link" id="r-cancel">Cancel</button>` : ""}</div></div>
    ${items.length ? `<div class="card">${group("Money in", moneyIn)}${group("Money out", moneyOut)}</div>`
      : `<div class="card empty">No recurring items yet. Add one above, pick from what's spotted in your history,
        or use ↻ on any transaction to start one from it.</div>`}
    ${suggestions.length ? `<div class="card"><h2>Spotted in your history</h2>${suggestions.map((s, i) => `<div class="acct-row sug-row"><div class="sug-inner">
      ${acctIcon(s.account_id) || `<span class="bank-icon letter">↻</span>`}
      <span class="acct-main"><span class="acct-title">${esc(s.name)}</span><span class="acct-sub">${nw(esc(s.frequency))} · ${nw(`${s.count}×`)} · ${nw(`last ${fmtDate(s.anchor_date)}`)}</span></span>
      <span class="acct-bal ${s.amount > 0 ? "pos" : ""}">${fmt(s.amount)}</span>
      <button class="btn add-sug" data-i="${i}">Add</button></div></div>`).join("")}</div>` : ""}`;

  $("#r-new-btn").addEventListener("click", () => { const f = $("#rec-new"); f.hidden = false; $(".r-name", f).focus(); });
  $("#r-cancel")?.addEventListener("click", () => { $("#rec-new").hidden = true; });
  $$(".rec-item").forEach((box) => box.addEventListener("toggle", () => {
    if (box.open) openRecurring.add(box.dataset.id); else openRecurring.delete(box.dataset.id);
  }));
  $$(".rec-item").forEach((box) => {
    const id = box.dataset.id;
    $(".r-freq", box).addEventListener("change", () => syncFreq(box));
    onEdit($$("input, select", box).filter((f) => {
      // Don't save a dates schedule until its dates are filled in.
      return true;
    }), async (f) => {
      const v = values(box);
      if ((v.frequency === "dates" || v.frequency === "semimonthly") && !v.dates.trim()) { if (f.classList.contains("r-freq")) $(".r-dates", box).focus(); return; }
      const r = await api(`/api/recurring/${id}`, { method: "POST", body: v });
      if (r.linked) toast(`Saved · matched ${r.linked} more`);
    });
    wireMissed(box);
    $(".r-del", box).addEventListener("click", async () => {
      if (!confirmInline($(".r-del", box), "Remove?")) return;
      await api(`/api/recurring/${id}`, { method: "DELETE" }); toast("Removed"); route();
    });
    $(".r-show", box)?.addEventListener("click", async (e) => {
      const btn = e.currentTarget, out = $(".r-matches", box);
      if (out.innerHTML) { out.innerHTML = ""; btn.textContent = "Show matched transactions"; return; }
      const data = await api(`/api/transactions?recurring=${id}&limit=50`);
      out.innerHTML = data.items.length ? `<table>${data.items.map((t) => `<tr><td class="muted">${fmtDate(t.posted)}</td><td>${esc(t.description)}</td>
        <td class="num">${fmt(t.amount)}</td></tr>`).join("")}</table>` : `<p class="help">No matched transactions.</p>`;
      btn.textContent = "Hide matched transactions";
    });
  });
  $(".r-freq", $("#rec-new")).addEventListener("change", () => syncFreq($("#rec-new")));
  $("#r-add").addEventListener("click", async () => {
    try { const r = await api("/api/recurring", { method: "POST", body: values($("#rec-new")) }); toast(r.linked ? `Added · matched ${r.linked} past transactions` : "Added"); route(); }
    catch (err) { toast(err.message, true); }
  });
  $$(".add-sug").forEach((b) => b.addEventListener("click", async () => {
    const s = suggestions[Number(b.dataset.i)];
    try { const r = await api("/api/recurring", { method: "POST", body: s }); toast(`Added · matched ${r.linked}`); route(); } catch (err) { toast(err.message, true); }
  }));
}

// Two-click confirm without a browser dialog: first click arms the button, second click within 4s confirms.
function confirmInline(btn, label) {
  if (btn.dataset.armed) return true;
  const orig = btn.textContent;
  btn.dataset.armed = "1"; btn.textContent = label;
  setTimeout(() => { delete btn.dataset.armed; btn.textContent = orig; }, 4000);
  return false;
}

// ------------------------------------------------------------------------------------------ budget
let budgetMonth = null;
async function renderBudget(el) {
  const now = new Date();
  budgetMonth = budgetMonth || `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  await loadCategories();
  const b = await api(`/api/budget?month=${budgetMonth}`);
  withPaths(b.categories);
  const [y, m] = b.month.split("-").map(Number);
  const label = new Date(y, m - 1, 1).toLocaleDateString("en-US", { month: "long", year: "numeric" });
  const shift = (n) => { const d = new Date(y, m - 1 + n, 1); budgetMonth = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`; renderBudget(el); };
  // Group into families: a top-level category plus everything under it. A category's "spent" already includes its subcategories.
  const families = [];
  for (const c of b.categories) {
    if (!c.depth) families.push({ top: c, kids: [] });
    else { const fam = families.find((f) => f.top.name === c.top); if (fam) fam.kids.push(c); }
  }
  const budgeted = new Set(b.categories.filter((c) => c.budget != null).map((c) => c.name));
  // Count a budget only when nothing above it has one, so nested budgets aren't counted twice.
  const countsToward = (c) => c.budget != null && !c.path.slice(0, -1).some((a) => budgeted.has(a));
  const isBudgeted = (f) => f.top.budget != null || f.kids.some((k) => k.budget != null);
  const inBudget = families.filter(isBudgeted);
  const notBudget = families.filter((f) => !isBudgeted(f) && (f.top.spent > 0.005));
  // Totals without double counting: a parent's budget covers its subcategories.
  let totBudget = 0, totSpent = 0;
  for (const f of inBudget) for (const c of [f.top, ...f.kids]) if (countsToward(c)) { totBudget += c.budget; totSpent += c.spent; }
  const allSpent = families.reduce((s, f) => s + Math.max(0, f.top.spent), 0);
  const otherSpent = allSpent - totSpent;
  const pace = b.day / b.days_in_month;  // share of the month gone
  const bar = (c) => {
    if (c.budget == null) return "";
    const pct = c.budget > 0 ? Math.max(0, c.spent / c.budget) : 0;
    const over = c.spent > c.budget;
    return `<div class="meter ${over ? "over" : ""}" role="img" aria-label="${Math.round(pct * 100)}% of budget used">
      <div class="meter-fill" style="width:${Math.min(100, pct * 100).toFixed(1)}%"></div>
      ${pace > 0 && pace < 1 ? `<div class="meter-pace" style="left:${(pace * 100).toFixed(1)}%" title="Where you'd be at an even pace today"></div>` : ""}</div>`;
  };
  const status = (c) => c.budget == null ? "" : c.spent > c.budget
    ? `<span class="over-label">▲ ${fmt(c.spent - c.budget)} over</span>`
    : pace > 0 && pace < 1 && c.spent > c.budget * pace * 1.1
      ? `<span class="muted">${fmt(c.left)} left · ahead of pace</span>` : `<span class="muted">${fmt(c.left)} left</span>`;
  const acctName = (id) => (b.pay_accounts.find((x) => x.id === id) || {}).name;
  // Which card a budget is paid with: out of the way until you want to change it.
  const payWith = (c) => {
    if (c.budget == null || !countsToward(c)) return "";
    const chosen = c.pay_with && acctName(c.pay_with);
    return `<button type="button" class="pay-btn ${chosen ? "" : "unset"}" title="Which card or account this spending goes on (used by the budget forecast)">${chosen ? esc(chosen) : "Set card"}</button>`;
  };
  const paySelect = (c) => {
    const usual = c.usual_account && acctName(c.usual_account);
    const opts = (kind) => b.pay_accounts.filter((x) => (kind === "credit") === (x.kind === "credit"))
      .map((x) => `<option value="${esc(x.id)}" ${x.id === c.pay_with ? "selected" : ""}>${esc(x.name)}</option>`).join("");
    return `<select class="b-pay" aria-label="Account ${esc(c.name)} is paid with">
      <option value="">${usual ? `Automatic (usually ${esc(usual)})` : "Automatic"}</option>
      <optgroup label="Cards">${opts("credit")}</optgroup><optgroup label="Bank accounts">${opts("cash")}</optgroup></select>`;
  };
  // One line per category: name, spent "of" budget (edited in place), then the bar underneath. Works at any width.
  const row = (c, sub, budgets) => `<div data-cat="${esc(c.name)}" class="brow ${sub ? "sub" : ""}">
    <div class="brow-top">
      <a href="#transactions" class="cat-link">${esc(c.name)}</a>${budgets ? payWith(c) : ""}
      <span class="brow-amt"><a href="#transactions" class="spent-link" title="See the transactions behind this amount">${fmt(c.spent)}</a>
        ${c.budget != null || budgets ? `<span class="muted">of</span>` : ""}
        <span class="money-input ${c.budget == null ? "blank" : ""}"><input type="number" min="0" step="10" class="b-amt ghost" value="${c.budget ?? ""}" placeholder="${c.budget == null ? (sub ? "—" : "Budget") : ""}" aria-label="Budget for ${esc(c.name)}"></span></span></div>
    ${c.budget != null ? `<div class="brow-bar">${bar(c)}<span class="small brow-status">${status(c)}</span></div>` : ""}</div>`;
  const familyRows = (f, budgets) => `<div class="family">${row(f.top, false, budgets)}${f.kids.filter((k) => budgets ? true : k.spent > 0.005).map((k) => row(k, true, budgets)).join("")}</div>`;
  const unusedTops = families.filter((f) => !isBudgeted(f) && !(f.top.spent > 0.005));

  el.innerHTML = `<div class="card-head"><h1>Budget</h1>
      <div class="seg"><button id="m-prev" aria-label="Previous month">‹</button><button class="on" disabled>${label}</button><button id="m-next" aria-label="Next month">›</button></div></div>
    <div class="tiles">
      <div class="tile"><div class="label">Budgeted</div><div class="value">${fmt0(totBudget)}</div><div class="sub">monthly · repeats every month</div></div>
      <div class="tile ${totSpent > totBudget && totBudget > 0 ? "alert" : ""}"><div class="label">Spent in budgeted categories</div><div class="value">${fmt0(totSpent)}</div>
        <div class="sub">${totBudget > 0 ? (totSpent > totBudget ? `▲ ${fmt0(totSpent - totBudget)} over` : `${fmt0(totBudget - totSpent)} left`) : "Set a budget below"}</div></div>
      <div class="tile"><div class="label">Other spending</div><div class="value">${fmt0(otherSpent + b.uncategorized)}</div>
        <div class="sub">${b.uncategorized > 0 ? `incl. ${fmt0(b.uncategorized)} uncategorized` : "in categories without a budget"}</div></div>
    </div>
    <div class="card"><h2>Budgets</h2>
      ${inBudget.length ? `<div class="blist">${inBudget.map((f) => familyRows(f, true)).join("")}</div>`
      : `<div class="empty">No budgets yet. Set one below.</div>`}
    </div>
    <div class="card"><h2>Not budgeted</h2>
      <div class="blist">${notBudget.map((f) => familyRows(f, false)).join("")}
        ${unusedTops.length ? `<div class="brow"><div class="brow-top"><select id="b-new-cat" class="ghost"><option value="">Another category…</option>${unusedTops.map((f) => [f.top, ...f.kids].map((c) => `<option value="${esc(c.name)}">${esc(c.parent ? `${c.parent} > ${c.name}` : c.name)}</option>`).join("")).join("")}</select>
          <span class="brow-amt"><span class="money-input blank"><input type="number" min="0" step="10" id="b-new-amt" class="b-amt ghost" placeholder="Budget"></span></span></div></div>` : ""}</div>
    </div>
    ${b.income ? `<p class="help">Money in this month: ${fmt(b.income)}</p>` : ""}`;

  $("#m-prev").addEventListener("click", () => shift(-1));
  $("#m-next").addEventListener("click", () => shift(1));
  const saveBudget = async (category, amount) => {
    try { await api("/api/budget", { method: "POST", body: { category, amount } }); toast(amount ? "Budget saved" : "Budget removed"); renderBudget(el); }
    catch (err) { toast(err.message, true); }
  };
  $$("[data-cat] .b-amt").forEach((input) => input.addEventListener("change", () => saveBudget(input.closest("[data-cat]").dataset.cat, input.value)));
  $$("[data-cat] .pay-btn").forEach((btn) => btn.addEventListener("click", () => {
    const cat = btn.closest("[data-cat]").dataset.cat, c = b.categories.find((x) => x.name === cat);
    btn.insertAdjacentHTML("afterend", paySelect(c));
    const sel = btn.nextElementSibling;
    btn.remove();
    sel.focus();
    let done = false;
    const finish = async (save) => {
      if (done) return; done = true;
      if (save && sel.value !== (c.pay_with || "")) {
        try { await api("/api/budget", { method: "POST", body: { category: cat, pay_with: sel.value } }); toast("Saved"); }
        catch (err) { toast(err.message, true); }
      }
      if (location.hash.startsWith("#budget")) renderBudget(el);   // not if you've moved to another page meanwhile
    };
    sel.addEventListener("change", () => finish(true));
    sel.addEventListener("blur", () => finish(true));
    sel.addEventListener("keydown", (e) => { if (e.key === "Escape") finish(false); });
  }));
  $("#b-new-amt")?.addEventListener("change", () => { const c = $("#b-new-cat").value; if (c) saveBudget(c, $("#b-new-amt").value); else toast("Choose a category first", true); });
  // The category name and the Spent amount both open Transactions showing exactly what adds up to that number.
  $$(".cat-link, .spent-link").forEach((a) => a.addEventListener("click", () => {
    Object.assign(LIST_STATE.transactions, { category: a.closest("[data-cat]").dataset.cat, q: "", account: "", month: b.month, scope: "budget" });
  }));
}

// ------------------------------------------------------------------------------------------ reports
let reportMonth = null;
async function renderReports(el) {
  const now = new Date();
  reportMonth = reportMonth || `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  const cf = await api(`/api/cashflow?month=${reportMonth}`);
  const [y, m] = cf.month.split("-").map(Number);
  const label = new Date(y, m - 1, 1).toLocaleDateString("en-US", { month: "long", year: "numeric" });
  const short = new Date(y, m - 1, 1).toLocaleDateString("en-US", { month: "long" });
  const shift = (n) => { const d = new Date(y, m - 1 + n, 1); reportMonth = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`; renderReports(el); };
  const empty = !cf.income.length && !cf.spending.length;
  el.innerHTML = `<div class="card-head"><h1>Where money went</h1>
      <div class="seg"><button id="r-prev" aria-label="Previous month">‹</button><button class="on" disabled>${label}</button><button id="r-next" aria-label="Next month">›</button></div></div>
    <div class="tiles">
      <div class="tile"><div class="label">Money in</div><div class="value">${fmt0(cf.total_in)}</div><div class="sub">income and refunds</div></div>
      <div class="tile"><div class="label">Money out</div><div class="value">${fmt0(cf.total_out)}</div><div class="sub">spending, not card payments or transfers</div></div>
      <div class="tile ${cf.net < 0 ? "alert" : ""}"><div class="label">${cf.net >= 0 ? "Left over" : "▲ Spent more than came in"}</div>
        <div class="value">${fmt0(Math.abs(cf.net))}</div><div class="sub">${cf.total_in > 0 ? `${Math.round((cf.net / cf.total_in) * 100)}% of money in` : ""}</div></div>
    </div>
    <div class="card"><h2>${short} cash flow</h2>
      ${empty ? `<div class="empty">No transactions in ${label}.</div>` : `<div class="scroll-x"><div class="sankey-wrap" id="sankey"></div></div>
      
      <details><summary class="small muted">Show as table</summary>${cashflowTable(cf)}</details>`}
    </div>`;
  $("#r-prev").addEventListener("click", () => shift(-1));
  $("#r-next").addEventListener("click", () => shift(1));
  if (!empty) drawSankey($("#sankey"), cf, short);
}

function cashflowTable(cf) {
  const pct = (v, t) => (t > 0 ? `${Math.round((v / t) * 100)}%` : "");
  return `<table style="max-width:560px">
    <tr><th>Money in</th><th class="num">Amount</th><th class="num">Share</th></tr>
    ${cf.income.map((n) => `<tr><td>${esc(n.name)}</td><td class="num">${fmt(n.value)}</td><td class="num muted">${pct(n.value, cf.total_in)}</td></tr>`).join("")}
    <tr><th>Money out</th><th class="num"></th><th class="num"></th></tr>
    ${cf.spending.map((n) => `<tr><td>${esc(n.name)}</td><td class="num">${fmt(n.value)}</td><td class="num muted">${pct(n.value, cf.total_out)}</td></tr>` +
      n.children.map((k) => `<tr class="sub-row"><td style="padding-left:28px"><span class="muted">${esc(n.name)} &gt;</span> ${esc(k.name)}</td><td class="num">${fmt(k.value)}</td><td class="num muted">${pct(k.value, cf.total_out)}</td></tr>`).join("")).join("")}
    <tr><td><b>${cf.net >= 0 ? "Left over" : "Spent more than came in"}</b></td><td class="num"><b>${fmt(Math.abs(cf.net))}</b></td><td></td></tr></table>`;
}

function drawSankey(host, cf, monthName) {
  const total = Math.max(cf.total_in, cf.total_out);
  if (total <= 0) return;
  // ---- nodes, by column
  const inNodes = cf.income.map((n) => ({ ...n, role: "in" }));
  if (cf.total_out > cf.total_in) inNodes.push({ name: "From your balance", value: +(cf.total_out - cf.total_in).toFixed(2), role: "neutral" });
  const hub = { name: monthName, value: total, role: "hub" };
  const small = cf.spending.filter((n) => n.value < cf.total_out * 0.02);
  const outNodes = cf.spending.filter((n) => n.value >= cf.total_out * 0.02).map((n) => ({ ...n, role: "out" }));
  if (small.length === 1) outNodes.push({ ...small[0], role: "out" });
  else if (small.length) outNodes.push({ name: `Everything else (${small.length})`, value: +small.reduce((s, n) => s + n.value, 0).toFixed(2),
    role: "out", children: [], members: small });
  if (cf.total_in > cf.total_out) outNodes.push({ name: "Left over", value: +(cf.total_in - cf.total_out).toFixed(2), role: "neutral" });
  const subNodes = [];
  for (const n of outNodes) for (const k of n.children || []) subNodes.push({ ...k, role: "out", parentNode: n });
  const cols = subNodes.length ? [inNodes, [hub], outNodes, subNodes] : [inNodes, [hub], outNodes];

  // ---- geometry
  const W = Math.max(720, host.clientWidth || 720);
  const nodeW = 12, pad = 8, top = 28, bottom = 10, minSlot = 16;  // every node gets at least one text line of room
  const left = 170, right = 190;
  const k = 300 / total;  // 300px of band height for the whole month
  const colX = cols.map((_, i) => left + (i * (W - left - right - nodeW)) / (cols.length - 1));
  const colHeight = (col) => col.reduce((s, n) => s + Math.max(minSlot, n.value * k), 0) + pad * (col.length - 1);
  const H = Math.max(360, Math.max(...cols.map(colHeight)) + top + bottom);
  cols.forEach((col, ci) => {
    let yy = top + (H - top - bottom - colHeight(col)) / 2;
    for (const n of col) {
      const slot = Math.max(minSlot, n.value * k);
      n.x = colX[ci]; n.h = Math.max(1.5, n.value * k); n.y = yy + (slot - n.h) / 2; n.out = 0; n.in = 0;
      yy += slot + pad;
    }
  });
  // ---- links
  const links = [];
  for (const n of inNodes) links.push({ s: n, t: hub, v: n.value, role: n.role === "in" ? "in" : "neutral" });
  for (const n of outNodes) links.push({ s: hub, t: n, v: n.value, role: n.role === "neutral" ? "neutral" : "out" });
  for (const c of subNodes) links.push({ s: c.parentNode, t: c, v: c.value, role: "out" });
  const band = (l) => {
    const t = l.v * k;
    const x0 = l.s.x + nodeW, x1 = l.t.x, xm = (x0 + x1) / 2;
    const y0 = l.s.y + l.s.out, y1 = l.t.y + l.t.in;
    l.s.out += t; l.t.in += t;
    return `M${x0},${y0} C${xm},${y0} ${xm},${y1} ${x1},${y1} L${x1},${y1 + t} C${xm},${y1 + t} ${xm},${y0 + t} ${x0},${y0 + t} Z`;
  };
  const pctOf = (v, base) => (base > 0 ? ` · ${Math.round((v / base) * 100)}%` : "");
  const tipFor = (name, v, role) => `<div class="tt-date">${esc(name)}</div><div class="tt-val">${fmt(v)}</div>` +
    `<div class="tt-ev"><span>${role === "in" ? "of money in" : role === "out" ? "of spending" : ""}</span><span>${role === "in" ? pctOf(v, cf.total_in).slice(3) : role === "out" ? pctOf(v, cf.total_out).slice(3) : ""}</span></div>`;

  let svg = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="Cash flow for ${esc(monthName)}: money in on the left, spending on the right">`;
  links.forEach((l, i) => { svg += `<path class="flow flow-${l.role}" d="${band(l)}" data-i="${i}"/>`; });
  const allNodes = cols.flat();
  allNodes.forEach((n, i) => {
    svg += `<rect class="node node-${n.role}" x="${n.x}" y="${n.y}" width="${nodeW}" height="${n.h}" rx="2" data-n="${i}"/>`;
  });
  // labels: money in on the left, the month above its bar, everything else to the right
  const text = (n, x, anchor, dy = 0) =>
    `<text class="s-label" x="${x}" y="${n.y + n.h / 2 + 4 + dy}" text-anchor="${anchor}">${esc(n.name)} <tspan class="s-amt">${fmt0(n.value)}</tspan></text>`;
  for (const n of inNodes) svg += text(n, n.x - 8, "end");
  svg += `<text class="s-label s-hub" x="${hub.x + nodeW / 2}" y="${hub.y - 10}" text-anchor="middle">${esc(monthName)} <tspan class="s-amt">${fmt0(cf.total_in)} in · ${fmt0(cf.total_out)} out</tspan></text>`;
  for (const n of outNodes) svg += text(n, n.x + nodeW + 8, "start");
  for (const n of subNodes) svg += text(n, n.x + nodeW + 8, "start");
  svg += `</svg><div class="tooltip" hidden></div>`;
  host.innerHTML = svg;

  const tip = $(".tooltip", host);
  const show = (html, ev) => {
    tip.innerHTML = html; tip.hidden = false;
    const r = host.getBoundingClientRect();
    tip.style.left = Math.min(ev.clientX - r.left + 14, r.width - tip.offsetWidth - 4) + "px";
    tip.style.top = Math.max(0, ev.clientY - r.top - 20) + "px";
  };
  $$(".flow", host).forEach((p) => {
    const l = links[Number(p.dataset.i)];
    p.addEventListener("mousemove", (ev) => show(tipFor(`${l.s.role === "hub" ? "" : l.s.name + " → "}${l.t.role === "hub" ? monthName : l.t.name}`, l.v, l.role), ev));
    p.addEventListener("mouseleave", () => (tip.hidden = true));
  });
  $$(".node", host).forEach((r) => {
    const n = allNodes[Number(r.dataset.n)];
    const extra = n.members ? `<div class="tt-ev" style="display:block">${n.members.map((mm) => nw(`${esc(mm.name)} ${fmt0(mm.value)}`)).join(" · ")}</div>` : "";
    r.addEventListener("mousemove", (ev) => show(tipFor(n.name, n.value, n.role) + extra, ev));
    r.addEventListener("mouseleave", () => (tip.hidden = true));
  });
}

// ------------------------------------------------------------------------------------------ investments
let invLiveTimer = null;
let invPeriod = "1Y", invAllocTab = "asset_class", invSort = { key: "value", dir: -1 }, invActivityLimit = 40, invActivityType = "";
const pct = (x, digits = 1) => {
  if (x == null) return "—";
  const shown = Math.abs(x * 100).toFixed(digits);
  return Number(shown) === 0 ? `${shown}%` : `${x > 0 ? "+" : "−"}${shown}%`;   // no "−0.0%"
};
const gainCls = (x) => (x == null ? "" : x > 0 ? "pos" : "");
const signed = (x) => (x == null ? "—" : `${x >= 0 ? "+" : "−"}${fmt(Math.abs(x))}`);

function invTiles(d, perf, beat) {
  return `
      <div class="tile"><div class="label">Total value</div><div class="value">${fmt0(d.total)}</div>
        <div class="sub">${d.accounts.filter((a) => !a.hidden).length} accounts · ${d.holdings.length} holdings</div></div>
      <div class="tile"><div class="label">Today</div><div class="value ${gainCls(d.day_change)}">${d.day_change == null ? "—" : signed(d.day_change)}</div>
        <div class="sub">${d.day_change_pct == null ? "no prices for today yet" : `${pct(d.day_change_pct, 2)} since the last close`}</div></div>
      <div class="tile"><div class="label">Total gain</div><div class="value ${gainCls(d.unrealized_gain)}">${d.unrealized_gain == null ? "—" : signed(d.unrealized_gain)}</div>
        <div class="sub">${d.cost_basis ? `${pct(d.unrealized_gain / d.cost_basis)} on ${fmt0(d.cost_basis)} invested` : "no cost basis yet"}${
          d.cost_missing ? ` · <a href="#" id="cost-missing-link">${d.cost_missing} holding${d.cost_missing === 1 ? "" : "s"} (${fmt0(d.cost_missing_value)}) need a cost basis</a>` : ""}</div></div>
      <div class="tile"><div class="label">Return · ${esc(invPeriod)}</div><div class="value">${pct(perf.return)}</div>
        <div class="sub">S&amp;P 500 ${pct(perf.benchmark_return)}${beat == null ? "" : beat >= 0 ? ` · ahead by ${pct(beat).slice(1)}` : ` · behind by ${pct(beat).slice(1)}`}</div></div>
`;
}

// Re-price holdings with live quotes and recompute the page totals.
function applyLiveQuotes(d, quotes, market) {
  const now = Date.now() / 1000;
  for (const h of d.holdings) {
    const q = h.ticker && quotes[h.ticker];
    h.live = false;
    if (!q || h.is_cash || !h.quantity) continue;
    // "Live" = trading now and quoted in the last 20 minutes. Mutual funds only get one price a day, after the close.
    h.live = market === "open" && (q.type || "").toUpperCase() !== "MUTUALFUND" && !!q.time && now - q.time < 1200;
    h.live_time = q.time;
    h.price = q.price;
    h.value = h.quantity * q.price;
    if (q.prev_close) {
      h.day_change = h.quantity * (q.price - q.prev_close);
      h.day_change_pct = q.price / q.prev_close - 1;
    }
    if (h.gain != null && h.cost_basis) {
      h.gain = h.value - h.cost_basis;
      h.gain_pct = h.gain / h.cost_basis;
    }
  }
  d.total = d.holdings.reduce((a, h) => a + h.value, 0);
  for (const h of d.holdings) h.allocation = d.total ? h.value / d.total : 0;
  const moved = d.holdings.filter((h) => h.day_change != null);
  d.day_change = moved.length ? moved.reduce((a, h) => a + h.day_change, 0) : null;
  const prev = moved.reduce((a, h) => a + h.value - h.day_change, 0);
  d.day_change_pct = d.day_change != null && prev > 0 ? d.day_change / prev : null;
  const known = d.holdings.filter((h) => h.gain != null);
  if (known.length) {
    d.unrealized_gain = known.reduce((a, h) => a + h.gain, 0);
    d.cost_basis = known.reduce((a, h) => a + h.cost_basis, 0);
  }
}

// Holdings you enter for an account that only reports a balance (a 401(k) through SimpleFIN, say).
async function openTrackedEditor(acctId, el) {
  const row = $(`.tr-row[data-id="${CSS.escape(acctId)}"]`);
  if (!row) return;
  if (!row.hidden) { row.hidden = true; return; }
  const t = await api(`/api/tracked/${encodeURIComponent(acctId)}`);
  const fund = (p = {}) => `<tr class="tr-fund">
      <td><input class="tr-ticker" value="${esc(p.ticker || "")}" placeholder="FXAIX" style="width:90px" aria-label="Ticker"></td>
      <td><input class="tr-name" value="${esc(p.ticker ? "" : (p.name || ""))}" placeholder="${p.ticker ? esc(p.name || "") : "Fund name (if no ticker)"}" style="width:220px" aria-label="Fund name"></td>
      <td><input class="tr-shares" type="number" min="0" step="0.0001" value="${p.ticker ? +(+p.shares).toFixed(4) : ""}" placeholder="shares" style="width:110px" aria-label="Shares"></td>
      <td><input class="tr-value" type="number" min="0" step="1" value="${!p.ticker && p.last_value ? Math.round(p.last_value) : ""}" placeholder="or value $" style="width:110px" aria-label="Value, for a fund without a ticker"></td>
      <td><input class="tr-pct" type="number" min="0" max="100" step="0.5" value="${p.pct ?? ""}" placeholder="%" style="width:70px" aria-label="Share of each contribution, %"></td>
      <td><button class="btn link tr-del" aria-label="Remove fund">✕</button></td></tr>`;
  row.hidden = false;
  row.firstElementChild.innerHTML = `<div class="card-inset tracked-editor">
    <h3>What this account holds</h3>
    <p class="help">Each fund's ticker and shares (or value, if it has no ticker) and your contribution split, from your plan's website.</p>
    <table class="tr-table"><tr><th>Ticker</th><th>Name</th><th>Shares</th><th>Value (no ticker)</th><th>Contribution %</th><th></th></tr>
      ${(t.positions.length ? t.positions : [{}]).map(fund).join("")}</table>
    <div class="form-row"><button class="btn link" id="tr-add">+ Add a fund</button><span class="small" id="tr-status"></span>
      <button class="btn link" id="tr-done" style="margin-left:auto">Done</button></div>
    ${t.contributions.length ? `<p class="small muted">Contributions spotted: ${t.contributions.slice(0, 6).map((c) => nw(`${fmtDate(c.date)} ${fmt(c.amount)}`)).join(" · ")}</p>` : ""}
  </div>`;
  const box = row.firstElementChild;
  let changed = false, timer;
  const status = (msg, bad) => { const s = $("#tr-status", box); s.textContent = msg; s.style.color = bad ? "var(--critical)" : ""; };
  const collect = () => $$(".tr-fund", box).map((r) => ({ ticker: $(".tr-ticker", r).value.trim(), name: $(".tr-name", r).value.trim(),
    shares: $(".tr-shares", r).value, value: $(".tr-value", r).value, pct: $(".tr-pct", r).value }))
    .filter((r) => r.ticker || r.name);
  const save = async () => {
    const rows = collect();
    if (!rows.length) return status("");
    const total = rows.reduce((a, r) => a + (Number(r.pct) || 0), 0);
    if (total > 0 && Math.abs(total - 100) > 0.5) return status(`Contribution percentages add up to ${total}%, not 100%`, true);
    if (rows.some((r) => !r.ticker && !Number(r.value))) return status("A fund without a ticker needs its current value", true);
    if (rows.some((r) => r.ticker && !Number(r.shares))) return status("Enter shares for each fund with a ticker", true);
    status("Saving…");
    try { await api(`/api/tracked/${encodeURIComponent(acctId)}`, { method: "POST", body: { rows } }); changed = true; status("Saved ✓ · prices updated"); }
    catch (err) { status(err.message, true); }
  };
  const wire = (r) => {
    $$("input", r).forEach((i) => i.addEventListener("change", () => { clearTimeout(timer); timer = setTimeout(save, 150); }));
    $(".tr-del", r).addEventListener("click", () => { r.remove(); save(); });
  };
  $$(".tr-fund", box).forEach(wire);
  $("#tr-add", box).addEventListener("click", () => {
    $(".tr-table", box).insertAdjacentHTML("beforeend", fund()); const r = $$(".tr-fund", box).pop(); wire(r); $(".tr-ticker", r).focus();
  });
  $("#tr-done", box).addEventListener("click", () => { row.hidden = true; if (changed) renderInvestments(el); });
  $(".tr-ticker", box).focus();
}

async function renderInvestments(el) {
  const status = await api("/api/plaid/status");
  if (!status.inv_accounts) {
    el.innerHTML = `<h1>Investments</h1><div class="card empty">
      <h2>No investment accounts yet</h2>
      <p>Link your brokerage and retirement accounts in <a href="#setup/connections">Settings → Connections</a>.</p></div>`;
    return;
  }
  const d = await api(`/api/investments?period=${invPeriod}`);
  const perf = d.performance || {};
  const h = d.history;
  const lastSync = [status.last_inv_sync, status.simplefin_last_sync].filter(Boolean).sort().pop();
  const synced = lastSync ? new Date(lastSync).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "never";
  const errors = status.items.filter((i) => i.error);
  const seenBy = Object.fromEntries(status.simplefin_seen.map((x) => [x.id || "", x]));
  const beat = perf.benchmark_return != null && perf.return != null ? perf.return - perf.benchmark_return : null;

  el.innerHTML = `<div class="card-head"><h1>Investments</h1>
      <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
        <span class="live-ind small muted" id="live-ind" title="Stock and ETF prices refresh every 30 seconds while the market is open">Holdings updated ${esc(synced)}</span>
        <div class="seg" id="inv-period">${["1M", "3M", "YTD", "1Y", "2Y"].map((p) => `<button data-p="${p}" class="${p === invPeriod ? "on" : ""}">${p}</button>`).join("")}</div></div></div>
    ${errors.map((i) => `<div class="warn critical"><span class="icon">!</span><span>${esc(i.institution_name || "A connection")} needs attention (${esc(i.error)}). <a href="#setup/connections">Reconnect in Settings</a></span></div>`).join("")}
    <div class="tiles tiles-4" id="inv-tiles">${invTiles(d, perf, beat)}</div>

    <div class="card"><div class="card-head"><h2>Value</h2></div>
      <div class="chart-wrap" id="inv-value"></div>
      <h3>Return vs S&amp;P 500</h3>
      <div class="chart-wrap" id="inv-return"></div>
      <div class="period-table scroll-x"><table><tr><th>Period</th>${Object.keys(d.periods).map((p) => `<th class="num">${p}</th>`).join("")}</tr>
        <tr><td>Your return</td>${Object.values(d.periods).map((p) => `<td class="num">${pct(p.return)}</td>`).join("")}</tr>
        <tr><td>S&amp;P 500</td>${Object.values(d.periods).map((p) => `<td class="num muted">${pct(p.benchmark_return)}</td>`).join("")}</tr>
        <tr><td>Gain after deposits</td>${Object.values(d.periods).map((p) => `<td class="num muted">${signed(p.gain)}</td>`).join("")}</tr></table></div>
    </div>

    <div class="card"><div class="card-head"><h2>Holdings</h2></div>
      <div class="scroll-x" id="inv-holdings"></div></div>

    <div class="grid-2">
      <div class="card"><div class="card-head"><h2>Allocation</h2>
        <div class="seg" id="alloc-tabs">${[["asset_class", "Asset class"], ["account", "Account"], ["sector", "Sector"], ["holding", "Top holdings"]]
          .map(([k, l]) => `<button data-k="${k}" class="${k === invAllocTab ? "on" : ""}">${l}</button>`).join("")}</div></div>
        <div id="inv-alloc"></div></div>
      <div class="card"><div class="card-head"><h2>X-ray</h2></div>
        <ul class="xray">${d.xray.map((r) => `<li class="${r.ok ? "ok" : r.info ? "info" : "warn-item"}"><span class="xr-icon">${r.ok ? "✓" : r.info ? "i" : "▲"}</span>
          <div><b>${esc(r.name)}</b> <span class="xr-state">${r.ok ? "looks fine" : r.info ? "note" : "worth a look"}</span><div class="small muted">${esc(r.detail)}</div></div></li>`).join("")}</ul></div>
    </div>

    <div class="grid-2" style="margin-top:20px">
      <div class="card"><div class="card-head"><h2>Dividends &amp; interest</h2><span class="small muted">${fmt(d.income.income_12m)} in the last 12 months · fees ${fmt(d.income.fees_12m)}</span></div>
        <div class="chart-wrap" id="inv-income"></div></div>
      <div class="card"><h2>Financial independence</h2>${fireForm(d.fire)}<div id="fire-out"></div></div>
    </div>

    <div class="card" style="margin-top:20px"><div class="card-head"><h2>Activity</h2>
        <select id="act-type"><option value="">All activity</option>${["buy", "sell", "cash", "fee", "transfer"].map((t) => `<option ${t === invActivityType ? "selected" : ""}>${t}</option>`).join("")}</select></div>
      <div class="scroll-x" id="inv-activity"></div></div>

    <div class="card"><h2>Accounts</h2>
      <div class="scroll-x"><table>${d.accounts.map((a) => `<tr><td><label class="inline"><input type="checkbox" class="inv-acct" data-id="${esc(a.id)}" ${a.hidden ? "" : "checked"}>
        ${esc(a.institution_name || "")} · ${esc(a.name || a.official_name || "")}${a.mask ? ` ••${esc(a.mask)}` : ""}</label></td>
        <td class="muted small">${a.source !== "simplefin" ? `via Plaid${a.subtype ? ` · ${esc(a.subtype)}` : ""}` : a.source === "simplefin" ? `via SimpleFIN${seenBy[a.id.slice(3)] ? ` · ${seenBy[a.id.slice(3)].positions ? `${seenBy[a.id.slice(3)].positions} positions` : "balance only"}` : ""}` : esc(a.subtype || "")}</td>
        <td class="num">${fmt(a.balance)}</td>
        <td class="num" style="white-space:nowrap">${a.source === "simplefin" && (a.tracked || (seenBy[a.id.slice(3)] && !seenBy[a.id.slice(3)].positions))
          ? `<button class="btn link tr-edit" data-id="${esc(a.id)}">${a.tracked ? "Edit holdings" : "Enter holdings"}</button>` : ""}</td></tr>
        ${a.tracked && a.drift > 0.02 ? `<tr><td colspan="4"><div class="warn"><span class="icon">!</span><span>${esc(a.name)}: the funds you entered are ${(a.drift * 100).toFixed(1)}% off the synced balance.
          Update the share counts from your latest statement.</span></div></td></tr>` : ""}
        <tr class="tr-row" data-id="${esc(a.id)}" hidden><td colspan="4"></td></tr>`).join("")}</table></div>
      ${status.simplefin_seen.length ? `<details><summary class="small">What SimpleFIN sends for each account</summary><table class="small">
        ${status.simplefin_seen.map((x) => `<tr><td>${esc(x.org || "")} · ${esc(x.name)}</td><td>${x.positions ? `${x.positions} positions` : "balance only, no positions"}</td>
          <td class="muted">${x.fields.length ? esc(x.fields.join(", ")) : ""}</td></tr>`).join("")}</table>
        </details>` : ""}</div>`;

  $$("#inv-period button").forEach((b) => b.addEventListener("click", () => { invPeriod = b.dataset.p; renderInvestments(el); }));
  $$(".tr-edit").forEach((b) => b.addEventListener("click", () => openTrackedEditor(b.dataset.id, el)));
  $$(".inv-acct").forEach((c) => c.addEventListener("change", async () => {
    await api(`/api/plaid/accounts/${encodeURIComponent(c.dataset.id)}`, { method: "POST", body: { hidden: !c.checked } }); renderInvestments(el);
  }));

  // charts
  const s = h.dates.findIndex((x) => x >= (perf.start || h.dates[0]));
  const sl = (arr) => arr.slice(Math.max(0, s));
  const dates = sl(h.dates);
  lineChart($("#inv-value"), dates, [
    { name: "Value", values: sl(h.value), cls: "s-main", area: true },
    { name: "Net invested", values: sl(h.invested), cls: "s-muted", step: true },
  ], { fmtY: shortMoney, fmtTip: fmt, height: 260, estimateUntil: h.estimated_before });
  const t0 = 1 + (h.twr[Math.max(0, s)] || 0), b0 = h.benchmark[Math.max(0, s)];
  lineChart($("#inv-return"), dates, [
    { name: "Your portfolio", values: sl(h.twr).map((r) => (1 + r) / t0 - 1), cls: "s-main" },
    { name: "S&P 500", values: sl(h.benchmark).map((b) => (b == null || b0 == null ? null : (1 + b) / (1 + b0) - 1)), cls: "s-alt" },
  ], { fmtY: (v) => pct(v, 0), fmtTip: (v) => pct(v, 2), height: 200, zero: true, estimateUntil: h.estimated_before });
  barChart($("#inv-income"), d.income.months.map((m) => { const [yy, mm] = m.split("-").map(Number); return new Date(yy, mm - 1, 1).toLocaleDateString("en-US", { month: "short" }) + (mm === 1 ? ` ${String(yy).slice(2)}` : ""); }),
    d.income.income, { fmtTip: fmt });

  const drawHoldings = () => {
    const rows = [...d.holdings].sort((a, b) => {
      const k = invSort.key, av = a[k] ?? -Infinity, bv = b[k] ?? -Infinity;
      return (typeof av === "string" ? av.localeCompare(bv) : av - bv) * invSort.dir;
    });
    const th = (k, label, cls = "num") => `<th class="${cls} sortable" data-k="${k}">${label}${invSort.key === k ? (invSort.dir < 0 ? " ↓" : " ↑") : ""}</th>`;
    $("#inv-holdings").innerHTML = `<table class="holdings"><tr>${th("name", "Holding", "")}${th("quantity", "Shares")}${th("price", "Price")}
      ${th("value", "Value")}${th("day_change", "Today")}${th("gain", "Total gain")}${th("allocation", "Weight")}${th("cost_basis", "Cost basis", "num hide-sm")}</tr>
      ${rows.map((x) => `<tr><td><div><b>${esc(x.ticker && !x.ticker.includes(":") ? x.ticker : "")}</b> ${esc(x.name || "")}</div><div class="desc">${esc(x.accounts.join(", "))}</div></td>
        <td class="num">${x.is_cash ? "—" : x.quantity.toLocaleString("en-US", { maximumFractionDigits: 4 })}</td>
        <td class="num" style="white-space:nowrap">${x.is_cash ? "—" : fmt(x.price)}${x.live ? `<i class="live-dot row-live" role="img" aria-label="Live price" title="Live price · ${esc(new Date(x.live_time * 1000).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", second: "2-digit" }))}"></i>` : ""}</td>
        <td class="num"><b>${fmt(x.value)}</b></td>
        <td class="num">${x.day_change == null ? `<span class="muted">—</span>`
          : `<span class="${gainCls(x.day_change)}">${signed(x.day_change)}</span><div class="desc">${pct(x.day_change_pct, 2)}</div>`}</td>
        <td class="num">${x.gain == null ? `<span class="muted">—</span>`
          : `<span class="${gainCls(x.gain)}">${signed(x.gain)}</span><div class="desc">${pct(x.gain_pct)}</div>`}</td>
        <td class="num"><div class="weight"><span style="width:${Math.min(100, x.allocation * 100).toFixed(1)}%"></span></div>${(x.allocation * 100).toFixed(1)}%</td>
        <td class="num hide-sm">${x.is_cash || x.asset_class === "Not reported" ? `<span class="muted">—</span>`
          : `<button class="cost-edit ${x.gain == null ? "missing" : ""}" data-sec="${esc(x.group || x.security_id)}" title="Edit cost basis">${x.gain == null ? "Add" : fmt(x.cost_basis)}${x.cost_manual ? ` <span class="tag">edited</span>` : ""} <span class="pencil" aria-hidden="true">✎</span></button>`}</td>
</tr>`).join("")}</table>`;
    $$("#inv-holdings .cost-edit").forEach((btn) => btn.addEventListener("click", () => openCostEditor(btn)));
    $$("#inv-holdings th.sortable").forEach((t) => t.addEventListener("click", () => {
      invSort = { key: t.dataset.k, dir: invSort.key === t.dataset.k ? -invSort.dir : (t.dataset.k === "name" ? 1 : -1) }; drawHoldings();
    }));
  };
  // Cost basis editor: one input per account holding the security (the institution's number is the default).
  const openCostEditor = (btn) => {
    const x = d.holdings.find((h) => (h.group || h.security_id) === btn.dataset.sec);
    const tr = btn.closest("tr");
    $$("#inv-holdings tr.cost-row").forEach((r) => r.remove());
    const row = document.createElement("tr");
    row.className = "cost-row";
    row.innerHTML = `<td colspan="8"><div class="cost-editor">
      <div class="small"><b>Price paid per share for ${esc(x.ticker || x.name || "")}</b> · your average if you bought at different prices.
        Runway multiplies it by the shares you hold. Leave a box empty to go back to what the institution reports.</div>
      ${x.lots.map((l, i) => `<div class="form-row"><label>${esc(l.account_name)} · ${Number(l.quantity).toLocaleString("en-US", { maximumFractionDigits: 4 })} shares
          <span class="cb-price"><span class="cb-cur" aria-hidden="true">$</span><input type="number" min="0" step="0.0001" class="cb-in" data-i="${i}"
            value="${l.manual && l.per_share != null ? +l.per_share.toFixed(4) : ""}" aria-label="Price per share in ${esc(l.account_name)}"
            placeholder="${l.reported_cost_basis && l.quantity ? `reported ${(l.reported_cost_basis / l.quantity).toFixed(2)}` : "not reported"}"></span></label>
        <span class="small muted cb-per" data-i="${i}"></span></div>`).join("")}
      <div class="form-row"><button class="btn link cb-cancel">Done</button></div></div></td>`;
    tr.after(row);
    const perShare = () => $$(".cb-in", row).forEach((inp) => {
      const l = x.lots[inp.dataset.i], v = Number(inp.value);
      $(`.cb-per[data-i="${inp.dataset.i}"]`, row).textContent = inp.value && l.quantity ? `= ${fmt(v * l.quantity)} cost basis` : "";
    });
    $$(".cb-in", row).forEach((inp) => inp.addEventListener("input", perShare));
    perShare();
    $(".cb-in", row)?.focus();
    let changed = false;
    const finish = () => { row.remove(); if (changed) renderInvestments(el); };
    $(".cb-cancel", row).addEventListener("click", finish);
    onEdit($$(".cb-in", row), async (inp) => {
      const l = x.lots[inp.dataset.i];
      await api("/api/investments/cost", { method: "POST", body: { account_id: l.account_id, security_id: l.security_id || x.security_id, per_share: inp.value } });
      changed = true;
      if (x.lots.length === 1) finish();   // one account: done as soon as it's saved
    });
  };
  drawHoldings();
  const wireTiles = () => $("#cost-missing-link")?.addEventListener("click", (e) => {
    e.preventDefault();
    invSort = { key: "gain", dir: 1 }; drawHoldings();
    $("#inv-holdings").scrollIntoView({ behavior: "smooth", block: "start" });
  });
  wireTiles();

  // Live prices: poll while this page is open. Every ~30s when the market is open, every 5 minutes when it's closed.
  clearTimeout(invLiveTimer);
  const liveTick = async () => {
    if (!$("#inv-tiles") || !location.hash.startsWith("#investments")) return;   // left the page
    let next = 30_000;
    if (!document.hidden) {
      try {
        const live = await api("/api/investments/live");
        if (!$("#inv-tiles")) return;
        applyLiveQuotes(d, live.quotes, live.market);
        $("#inv-tiles").innerHTML = invTiles(d, perf, beat); wireTiles();
        if (!document.querySelector(".cost-row")) drawHoldings();   // don't close an open cost editor
        const t = new Date(live.as_of).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", second: "2-digit" });
        $("#live-ind").innerHTML = live.market === "open"
          ? `<i class="live-dot" aria-hidden="true"></i> Live prices · ${esc(t)}`
          : `Market closed · latest prices as of ${esc(t)}`;
        $("#live-ind").classList.toggle("is-live", live.market === "open");
        if (live.market !== "open") next = 300_000;
      } catch (err) { /* keep the last numbers; try again next time */ }
    }
    invLiveTimer = setTimeout(liveTick, next);
  };
  liveTick();

  const drawAlloc = () => {
    const items = d.allocation[invAllocTab] || [];
    $("#inv-alloc").innerHTML = items.length ? `<table class="alloc">${items.map((a) => `<tr><td>${esc(a.name)}</td>
      <td style="width:45%"><div class="weight wide"><span style="width:${Math.min(100, a.share * 100).toFixed(1)}%"></span></div></td>
      <td class="num">${(a.share * 100).toFixed(1)}%</td><td class="num muted">${fmt0(a.value)}</td></tr>`).join("")}</table>` : `<div class="empty">Nothing to show.</div>`;
  };
  drawAlloc();
  $$("#alloc-tabs button").forEach((b) => b.addEventListener("click", () => { invAllocTab = b.dataset.k; $$("#alloc-tabs button").forEach((x) => x.classList.toggle("on", x === b)); drawAlloc(); }));

  const drawActivity = () => {
    const rows = d.activity.filter((t) => !invActivityType || t.type === invActivityType);
    const shown = rows.slice(0, invActivityLimit);
    $("#inv-activity").innerHTML = shown.length ? `<table><tr><th>Date</th><th>Activity</th><th class="hide-sm">Account</th><th class="num">Shares</th><th class="num">Price</th><th class="num">Cash</th></tr>
      ${shown.map((t) => `<tr><td class="muted" style="white-space:nowrap">${fmtDate(t.date, { month: "short", day: "numeric", year: "numeric" })}</td>
        <td><div>${esc(t.name || "")}</div><div class="desc">${esc(t.type || "")}${t.subtype && t.subtype !== t.type ? ` · ${esc(t.subtype)}` : ""}${t.ticker && !t.ticker.includes(":") ? ` · ${esc(t.ticker)}` : ""}</div></td>
        <td class="muted hide-sm">${esc(t.account_name)}</td>
        <td class="num">${t.quantity ? t.quantity.toLocaleString("en-US", { maximumFractionDigits: 4 }) : ""}</td>
        <td class="num muted">${t.price ? fmt(t.price) : ""}</td>
        <td class="num ${t.amount < 0 ? "pos" : ""}">${t.amount ? signed(-t.amount) : ""}</td></tr>`).join("")}</table>
      ${rows.length > shown.length ? `<button class="btn link" id="act-more">Show more (${rows.length - shown.length})</button>` : ""}` : `<div class="empty">No activity.</div>`;
    $("#act-more")?.addEventListener("click", () => { invActivityLimit += 100; drawActivity(); });
  };
  drawActivity();
  $("#act-type").addEventListener("change", (e) => { invActivityType = e.target.value; invActivityLimit = 40; drawActivity(); });
  wireFire(d.fire);
}

function fireForm(f) {
  return `<p class="help">Target: 25× yearly spending (the 4% rule).</p>
    <div class="form-row fire-form">
      <label>Yearly spending<input type="number" id="fi-spend" value="${Math.round(f.annual_spending)}" step="1000"></label>
      <label>Saved per year<input type="number" id="fi-save" value="${Math.round(f.yearly_savings)}" step="1000"></label>
      <label>Return after inflation<input type="number" id="fi-ret" value="${(f.expected_return * 100).toFixed(1)}" step="0.5"></label>
      <label>Withdrawal rate<input type="number" id="fi-wr" value="${(f.withdrawal_rate * 100).toFixed(1)}" step="0.25"></label>
    </div>`;
}

function wireFire(f) {
  const calc = () => {
    const spend = Number($("#fi-spend").value) || 0, save = Number($("#fi-save").value) || 0;
    const r = (Number($("#fi-ret").value) || 0) / 100, wr = (Number($("#fi-wr").value) || 4) / 100;
    const target = wr > 0 ? spend / wr : 0;
    let v = f.current, years = 0;
    const path = [v];
    while (v < target && years < 60) { v = v * (1 + r) + save; years += 1; path.push(v); }
    const out = $("#fire-out");
    if (!out) return;
    const reached = v >= target;
    out.innerHTML = `<div class="fire-result"><div><div class="label small muted">Target</div><div class="big">${fmt0(target)}</div></div>
      <div><div class="label small muted">You have</div><div class="big">${fmt0(f.current)}</div><div class="small muted">${target ? ((f.current / target) * 100).toFixed(0) : 0}% of the way</div></div>
      <div><div class="label small muted">${reached ? "Reached in about" : "Not reached within"}</div><div class="big">${reached ? `${years} yr${years === 1 ? "" : "s"}` : "60 yrs"}</div>
        <div class="small muted">${reached && years ? `around ${new Date().getFullYear() + years}` : reached ? "already there" : "try saving more"}</div></div></div>
      <div class="chart-wrap" id="fire-chart"></div>`;
    const yearsLabels = path.map((_, i) => `${new Date().getFullYear() + i}`);
    lineChart($("#fire-chart"), yearsLabels, [
      { name: "Projected", values: path, cls: "s-main", area: true },
      { name: "Target", values: path.map(() => target), cls: "s-muted" },
    ], { fmtY: shortMoney, fmtTip: fmt, height: 170, labels: true });
  };
  $$(".fire-form input").forEach((i) => i.addEventListener("input", calc));
  calc();
}

// A small reusable line chart: shared y axis, direct labels at the line ends, crosshair tooltip.
function lineChart(host, xs, series, opts = {}) {
  if (!host) return;
  const n = xs.length;
  if (n < 2) { host.innerHTML = `<div class="empty small">Not enough history yet.</div>`; return; }
  const W = Math.max(320, host.clientWidth), H = opts.height || 240;
  const m = { top: 14, right: 110, bottom: 26, left: 56 };
  const iw = W - m.left - m.right, ih = H - m.top - m.bottom;
  const vals = series.flatMap((s) => s.values.filter((v) => v != null && isFinite(v)));
  if (!vals.length) { host.innerHTML = `<div class="empty small">No data for this period yet.</div>`; return; }
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (opts.zero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
  if (lo === hi) { lo -= Math.abs(lo) * 0.1 || 1; hi += Math.abs(hi) * 0.1 || 1; }
  const ticks = niceTicks(lo, hi, 4);
  const y0 = ticks[0], y1 = ticks[ticks.length - 1];
  const x = (i) => m.left + (i / (n - 1)) * iw;
  const y = (v) => m.top + (1 - (v - y0) / (y1 - y0 || 1)) * ih;
  const fy = opts.fmtY || ((v) => v), ft = opts.fmtTip || fy;
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(series.map((s) => s.name).join(" and "))}">`;
  svg += `<g class="grid">${ticks.map((t) => `<line x1="${m.left}" x2="${W - m.right}" y1="${y(t)}" y2="${y(t)}"/>`).join("")}</g>`;
  svg += `<g class="axis">${ticks.map((t) => `<text x="${m.left - 8}" y="${y(t) + 4}" text-anchor="end">${fy(t)}</text>`).join("")}</g>`;
  if (opts.zero && y0 < 0 && y1 > 0) svg += `<line class="zero" x1="${m.left}" x2="${W - m.right}" y1="${y(0)}" y2="${y(0)}"/>`;
  // x labels: ~6 evenly spaced
  const step = Math.max(1, Math.round((n - 1) / Math.max(1, Math.min(5, Math.floor(iw / 80)))));
  let xl = "";
  for (let i = 0; i < n; i += step) {
    const lab = opts.labels ? xs[i] : fmtDate(xs[i], n > 200 ? { month: "short", year: "2-digit" } : { month: "short", day: "numeric" });
    xl += `<text x="${x(i)}" y="${H - 6}" text-anchor="${i === 0 ? "start" : "middle"}">${esc(lab)}</text>`;
  }
  svg += `<g class="axis">${xl}</g>`;
  if (opts.estimateUntil && xs[0] < opts.estimateUntil) {
    let ei = xs.findIndex((d) => d >= opts.estimateUntil);
    if (ei < 0) ei = n - 1;
    const ex = x(ei);
    svg += `<rect class="est-zone" x="${m.left}" y="${m.top}" width="${Math.max(0, ex - m.left)}" height="${ih}"/>
      <line class="est-line" x1="${ex}" x2="${ex}" y1="${m.top}" y2="${m.top + ih}"/>
      ${ex - m.left > 70 ? `<text class="est-label" x="${ex - 6}" y="${m.top + 12}" text-anchor="end">estimated</text>` : ""}`;
  }
  const ends = [];
  for (const s of series) {
    let d = "", started = false, prev = null;
    s.values.forEach((v, i) => {
      if (v == null || !isFinite(v)) { started = false; return; }
      if (s.step && started && prev != null) d += ` L${x(i).toFixed(1)},${y(prev).toFixed(1)}`;
      d += `${started ? " L" : " M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
      started = true; prev = v;
    });
    if (s.area) {
      const first = s.values.findIndex((v) => v != null);
      svg += `<path class="area ${s.cls}" d="${d} L${x(n - 1)},${y(y0)} L${x(first)},${y(y0)} Z"/>`;
    }
    svg += `<path class="line ${s.cls}" d="${d}"/>`;
    const li = s.values.map((v, i) => (v != null ? i : -1)).filter((i) => i >= 0).pop();
    if (li != null) ends.push({ s, yv: y(s.values[li]), v: s.values[li], xi: x(li) });
  }
  // end labels, nudged apart so they don't overlap
  ends.sort((a, b) => a.yv - b.yv);
  for (let i = 1; i < ends.length; i++) if (ends[i].yv - ends[i - 1].yv < 15) ends[i].yv = ends[i - 1].yv + 15;
  // pushed below the chart? slide the whole stack back up
  const over = ends.length ? ends[ends.length - 1].yv - (m.top + ih) : 0;
  if (over > 0) ends.forEach((e) => { e.yv -= over; });
  for (let i = ends.length - 2; i >= 0; i--) if (ends[i + 1].yv - ends[i].yv < 15) ends[i].yv = ends[i + 1].yv - 15;
  for (const e of ends) svg += `<circle class="end-dot ${e.s.cls}" cx="${e.xi}" cy="${y(e.v)}" r="4"/>
    <text class="end-label" x="${e.xi + 8}" y="${e.yv + 4}">${esc(e.s.name)}</text>`;
  svg += `<g class="hover" style="display:none"><line class="cross" y1="${m.top}" y2="${m.top + ih}"/></g>`;
  svg += `<rect class="hit" x="${m.left}" y="${m.top}" width="${iw}" height="${ih}" fill="transparent"/></svg><div class="tooltip" hidden></div>`;
  host.innerHTML = svg;
  const svgEl = $("svg", host), hover = $(".hover", host), tip = $(".tooltip", host);
  const move = (cx) => {
    const r = svgEl.getBoundingClientRect();
    const px = ((cx - r.left) / r.width) * W;
    const i = Math.max(0, Math.min(n - 1, Math.round(((px - m.left) / iw) * (n - 1))));
    hover.style.display = ""; $("line", hover).setAttribute("x1", x(i)); $("line", hover).setAttribute("x2", x(i));
    tip.innerHTML = `<div class="tt-date">${esc(opts.labels ? xs[i] : fmtDow(xs[i]))}${opts.estimateUntil && xs[i] < opts.estimateUntil ? " · estimate" : ""}</div>` + series.map((s) =>
      `<div class="tt-ev"><span><i class="key ${s.cls}"></i>${esc(s.name)}</span><span>${s.values[i] == null ? "—" : ft(s.values[i])}</span></div>`).join("");
    tip.hidden = false;
    const sx = (x(i) / W) * r.width;
    tip.style.left = Math.min(Math.max(0, sx + 12), r.width - tip.offsetWidth) + "px";
    tip.style.top = "0px";
  };
  const hit = $(".hit", host);
  hit.addEventListener("mousemove", (e) => move(e.clientX));
  hit.addEventListener("touchmove", (e) => { move(e.touches[0].clientX); e.preventDefault(); }, { passive: false });
  hit.addEventListener("mouseleave", () => { hover.style.display = "none"; tip.hidden = true; });
}

// Single-series column chart (monthly income).
function barChart(host, labels, values, opts = {}) {
  if (!host) return;
  const W = Math.max(300, host.clientWidth), H = opts.height || 200;
  const m = { top: 12, right: 8, bottom: 24, left: 48 };
  const iw = W - m.left - m.right, ih = H - m.top - m.bottom;
  const hi = Math.max(...values, 0);
  if (hi <= 0) { host.innerHTML = `<div class="empty small">No dividends or interest recorded yet.</div>`; return; }
  const ticks = niceTicks(0, hi, 3), y1 = ticks[ticks.length - 1];
  const bw = iw / values.length, w = Math.min(24, bw - 2);
  const y = (v) => m.top + (1 - v / y1) * ih;
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Monthly dividends and interest">`;
  svg += `<g class="grid">${ticks.map((t) => `<line x1="${m.left}" x2="${W - m.right}" y1="${y(t)}" y2="${y(t)}"/>`).join("")}</g>`;
  svg += `<g class="axis">${ticks.map((t) => `<text x="${m.left - 6}" y="${y(t) + 4}" text-anchor="end">${shortMoney(t)}</text>`).join("")}</g>`;
  values.forEach((v, i) => {
    const cx = m.left + bw * i + bw / 2;
    if (v > 0) {
      const top = y(v), h = m.top + ih - top, r = Math.min(4, h);
      svg += `<path class="bar" data-i="${i}" d="M${cx - w / 2},${m.top + ih} V${top + r} Q${cx - w / 2},${top} ${cx - w / 2 + r},${top} H${cx + w / 2 - r} Q${cx + w / 2},${top} ${cx + w / 2},${top + r} V${m.top + ih} Z"/>`;
    }
    if (i % Math.ceil(values.length / 8) === 0) svg += `<text class="axis-t" x="${cx}" y="${H - 6}" text-anchor="middle">${esc(labels[i])}</text>`;
    svg += `<rect class="bar-hit" data-i="${i}" x="${m.left + bw * i}" y="${m.top}" width="${bw}" height="${ih}" fill="transparent"/>`;
  });
  svg += `</svg><div class="tooltip" hidden></div>`;
  host.innerHTML = svg;
  const tip = $(".tooltip", host);
  $$(".bar-hit", host).forEach((r) => {
    r.addEventListener("mousemove", (e) => {
      const i = Number(r.dataset.i), box = host.getBoundingClientRect();
      tip.innerHTML = `<div class="tt-date">${esc(labels[i])}</div><div class="tt-val">${(opts.fmtTip || fmt)(values[i])}</div>`;
      tip.hidden = false; tip.style.left = Math.min(e.clientX - box.left + 10, box.width - tip.offsetWidth) + "px"; tip.style.top = "0px";
    });
    r.addEventListener("mouseleave", () => (tip.hidden = true));
  });
}

// ------------------------------------------------------------------------------------------ net worth
const ASSET_KIND_LABEL = { home: "Home / property", vehicle: "Vehicle", other: "Other" };
const assetLookupLink = (a) => a.url ? { href: a.url, label: a.url.includes("zillow") ? "Zillow" : a.url.includes("kbb") ? "KBB" : "Link" }
  : a.kind === "home" && a.address ? { href: `https://www.zillow.com/homes/${encodeURIComponent(a.address)}_rb/`, label: "Zillow" }
  : a.kind === "vehicle" ? { href: "https://www.kbb.com/whats-my-car-worth/", label: "KBB" } : null;

async function renderNetWorth(el) {
  const d = await api("/api/networth");
  const ch = d.change["30d"];
  const since = d.first_snapshot ? fmtDate(d.first_snapshot, { month: "short", day: "numeric", year: "numeric" }) : null;
  const sideRows = (side) => d.groups.filter((g) => g.side === side).map((g) => `
      <tr class="nw-group"><td><b>${esc(g.label)}</b></td><td class="num"><b>${fmt(g.total)}</b></td></tr>
      ${g.items.map((i) => `<tr class="sub-row"><td style="padding-left:24px">${i.type === "account" ? acctLabel(i.id, i.name) : esc(i.name)}
          <div class="desc">${i.type === "account" ? esc(i.org || "") : `${i.source === "rentcast" ? "RentCast estimate" : "Your estimate"} · ${fmtDate(i.as_of)}`}${
            i.equity != null ? ` · ${fmt(i.equity)} equity after ${esc(i.loan.name)}` : ""}</div></td>
        <td class="num">${fmt(i.value)}</td></tr>`).join("")}`).join("");
  const assetGroups = d.groups.filter((g) => g.side === "asset" && g.total > 0);
  el.innerHTML = `<h1>Net worth</h1>
    <div class="tiles">
      <div class="tile"><div class="label">Net worth</div><div class="value">${fmt0(d.net)}</div>
        <div class="sub">${ch != null ? `${signed(ch)} in the last 30 days` : since ? `tracking since ${esc(since)}` : ""}</div></div>
      <div class="tile"><div class="label">Assets</div><div class="value">${fmt0(d.assets)}</div>
        <div class="sub">${assetGroups.map((g) => esc(g.label)).join(" · ")}</div></div>
      <div class="tile"><div class="label">Liabilities</div><div class="value">${fmt0(d.liabilities)}</div>
        <div class="sub">${d.groups.filter((g) => g.side === "liability").map((g) => nw(`${esc(g.label)} ${fmt0(g.total)}`)).join(" · ") || "nothing owed"}</div></div>
    </div>
    <div class="card"><div class="card-head"><h2>Over time</h2></div>
      <div class="chart-wrap" id="nw-chart"></div>
      ${d.history.length < 2 ? `<p class="help">Fills in as the days go by.</p>` : ""}</div>
    <div class="card"><h2>What makes it up</h2>
      <div class="nw-bar" role="img" aria-label="Share of assets by type">${assetGroups.map((g, i) =>
        `<span class="nw-seg s${i}" style="width:${(g.total / d.assets * 100).toFixed(2)}%" title="${esc(g.label)} ${fmt0(g.total)}"></span>`).join("")}</div>
      <div class="nw-legend">${assetGroups.map((g, i) => `<span><i class="nw-key s${i}"></i>${esc(g.label)} ${(g.total / d.assets * 100).toFixed(0)}%</span>`).join("")}</div>
      <div class="grid-2" style="margin-top:12px">
        <div><h3>Assets</h3><table class="nw-table">${sideRows("asset")}</table></div>
        <div><h3>Liabilities</h3><table class="nw-table">${sideRows("liability") || `<tr><td class="muted">Nothing owed</td></tr>`}</table></div>
      </div></div>
    <div class="card"><div class="card-head"><h2>Home, vehicles and other assets</h2>
        <button class="btn primary" id="asset-new">Add an asset</button></div>
      
      <div id="asset-form-host"></div>
      <div class="asset-list">${d.assets_list.length ? d.assets_list.map((a) => assetCard(a, d)).join("") : `<div class="empty">No assets yet.</div>`}</div>
    </div>`;

  if (d.history.length >= 2) {
    lineChart($("#nw-chart"), d.history.map((h) => h.date), [{ name: "Net worth", values: d.history.map((h) => h.net), cls: "s-main", area: true }],
      { fmtY: shortMoney, fmtTip: fmt, height: 220 });
  } else {
    $("#nw-chart").innerHTML = `<div class="empty small">Today: ${fmt0(d.net)}</div>`;
  }
  $("#asset-new").addEventListener("click", () => openAssetForm(null, d, el));
  $$(".asset-card").forEach((card) => {
    const a = d.assets_list.find((x) => String(x.id) === card.dataset.id);
    $(".a-edit", card).addEventListener("click", () => openAssetForm(a, d, el));
    $(".a-update", card).addEventListener("click", () => {
      const box = $(".a-quick", card);
      box.innerHTML = `<div class="form-row"><label>New value<span class="cb-price"><span class="cb-cur" aria-hidden="true">$</span><input type="number" min="0" step="100" class="a-val" value="${Math.round(a.current_value)}"></span></label>
        <button class="btn link a-val-cancel">Cancel</button></div>`;
      $(".a-val", box).focus(); $(".a-val", box).select();
      $(".a-val-cancel", box).addEventListener("click", () => (box.innerHTML = ""));
      onEdit([$(".a-val", box)], async () => {
        await api(`/api/assets/${a.id}`, { method: "POST", body: { value: $(".a-val", box).value } }); toast("Value updated"); renderNetWorth(el);
      });
      $(".a-val", box).addEventListener("keydown", (e) => { if (e.key === "Escape") box.innerHTML = ""; });
    });
    $(".a-refresh", card)?.addEventListener("click", async (e) => {
      const b = e.currentTarget; b.disabled = true; b.textContent = "Looking up…";
      try { const r = await api(`/api/assets/${a.id}/refresh`, { method: "POST" }); toast(`RentCast estimate: ${fmt0(r.value)} (range ${fmt0(r.low)}–${fmt0(r.high)})`); renderNetWorth(el); }
      catch (err) { toast(err.message, true); b.disabled = false; b.textContent = "Update from RentCast"; }
    });
    $(".a-remove", card).addEventListener("click", async (e) => {
      if (!confirmInline(e.currentTarget, `Remove ${a.name}?`)) return;
      await api(`/api/assets/${a.id}/remove`, { method: "POST" }); toast("Removed"); renderNetWorth(el);
    });
  });
}

function assetCard(a, d) {
  const link = assetLookupLink(a);
  const loan = d.loan_accounts.find((l) => l.id === a.loan_account_id);
  const item = d.groups.flatMap((g) => g.items).find((i) => i.type === "asset" && i.id === a.id);
  const stale = a.as_of && (Date.now() - parseDate(a.as_of)) / 864e5 > (a.kind === "home" ? 120 : 90);
  return `<div class="asset-card" data-id="${a.id}">
    <div class="asset-main">
      <div><div class="asset-name">${esc(a.name)} <span class="tag">${esc(ASSET_KIND_LABEL[a.kind] || a.kind)}</span></div>
        <div class="small muted">${a.source === "rentcast" ? `RentCast estimate${a.low && a.high ? ` (range ${fmt0(a.low)}–${fmt0(a.high)})` : ""}` : "Your estimate"}
          · set ${fmtDate(a.as_of, { month: "short", day: "numeric", year: "numeric" })}${a.yearly_change ? ` · ${a.yearly_change > 0 ? "+" : "−"}${Math.abs(a.yearly_change)}% a year since` : ""}
          ${stale ? ` · <span class="stale">worth a fresh look</span>` : ""}</div>
        ${a.address ? `<div class="small muted">${esc(a.address)}</div>` : ""}
        ${loan ? `<div class="small">${fmt(item?.equity ?? 0)} equity after ${esc(loan.name)} (${fmt0(item?.loan?.owed ?? 0)} owed)</div>` : ""}</div>
      <div class="asset-value">${fmt0(a.current_value)}</div>
    </div>
    <div class="asset-actions">
      <button class="btn a-update">Update value</button>
      ${a.kind === "home" && d.rentcast.configured && a.address ? `<button class="btn a-refresh">Update from RentCast</button>` : ""}
      ${link ? `<a class="btn link" href="${esc(link.href)}" target="_blank" rel="noopener">Check on ${esc(link.label)} ↗</a>` : ""}
      <button class="btn link a-edit">Edit details</button><button class="btn link a-remove">Remove</button>
    </div>
    <div class="a-quick"></div>
  </div>`;
}

function openAssetForm(a, d, el) {
  const host = $("#asset-form-host");
  const v = a || { kind: "home" };
  host.innerHTML = `<div class="asset-form card-inset">
    <h3>${a ? `Edit ${esc(a.name)}` : "Add an asset"}</h3>
    <div class="form-row">
      <label>What is it<select id="af-kind">${Object.entries(ASSET_KIND_LABEL).map(([k, l]) => `<option value="${k}" ${v.kind === k ? "selected" : ""}>${l}</option>`).join("")}</select></label>
      <label>Name<input id="af-name" value="${esc(v.name || "")}" placeholder="e.g. House, 2022 Model Y"></label>
      ${a ? "" : `<label>Value today<span class="cb-price"><span class="cb-cur" aria-hidden="true">$</span><input id="af-value" type="number" min="0" step="100"></span></label>`}
    </div>
    <div class="form-row af-home">
      <label style="flex:1">Address (street, city, state, zip)<input id="af-address" value="${esc(v.address || "")}" style="width:100%"></label>
      <label class="inline"><input type="checkbox" id="af-auto" ${v.auto_update ? "checked" : ""} ${d.rentcast.configured ? "" : "disabled"}> Update from RentCast monthly</label>
    </div>
    <div class="form-row">
      <label>Yearly change %<input id="af-yc" type="number" step="0.5" value="${v.yearly_change ?? ""}" placeholder="e.g. -15 for a car" style="width:150px"></label>
      <label>Loan against it<select id="af-loan"><option value="">None</option>${d.loan_accounts.map((l) => `<option value="${esc(l.id)}" ${v.loan_account_id === l.id ? "selected" : ""}>${esc(l.name)}</option>`).join("")}</select></label>
      <label style="flex:1">Link to check the value (Zillow, KBB…)<input id="af-url" value="${esc(v.url || "")}" placeholder="https://" style="width:100%"></label>
    </div>
    <div class="form-row">${a ? `<button class="btn link" id="af-cancel">Done</button>`
      : `<button class="btn primary" id="af-save">Add</button><button class="btn link" id="af-cancel">Cancel</button>`}</div>
  </div>`;
  const syncKind = () => { $(".af-home", host).style.display = $("#af-kind").value === "home" ? "" : "none"; };
  $("#af-kind").addEventListener("change", syncKind); syncKind();
  $("#af-name").focus();
  let changed = false;
  $("#af-cancel").addEventListener("click", () => { host.innerHTML = ""; if (changed) renderNetWorth(el); });
  if (a) {
    const fieldKey = { "af-name": "name", "af-kind": "kind", "af-yc": "yearly_change", "af-loan": "loan_account_id", "af-url": "url", "af-address": "address", "af-auto": "auto_update" };
    onEdit($$("input, select", host), async (f) => {
      await api(`/api/assets/${a.id}`, { method: "POST", body: { [fieldKey[f.id]]: f.type === "checkbox" ? f.checked : f.value } });
      changed = true;
    });
    return;
  }
  $("#af-save").addEventListener("click", async () => {
    const body = { name: $("#af-name").value, kind: $("#af-kind").value, yearly_change: $("#af-yc").value,
      loan_account_id: $("#af-loan").value, url: $("#af-url").value, address: $("#af-address").value, auto_update: $("#af-auto").checked };
    if (!a) body.value = $("#af-value").value;
    try {
      await api(a ? `/api/assets/${a.id}` : "/api/assets", { method: "POST", body });
      toast(a ? "Saved" : `Added ${body.name}`); renderNetWorth(el);
    } catch (err) { toast(err.message, true); }
  });
  host.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

// Plaid Link: loaded from Plaid's servers only when you connect an account.
function loadPlaid() {
  if (window.Plaid) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const sc = document.createElement("script");
    sc.src = "https://cdn.plaid.com/link/v2/stable/link-initialize.js";
    sc.onload = resolve; sc.onerror = () => reject(new Error("Couldn't load Plaid Link. Check your internet connection."));
    document.head.appendChild(sc);
  });
}

async function openPlaidLink(itemId, kind = "investments") {
  await loadPlaid();
  const lt = await api("/api/plaid/link_token", { method: "POST", body: { item_id: itemId || null, kind } });
  if (kind === "bank" && lt.kind === "cards") toast("Your Plaid account doesn't have Transactions, so this connects card statements only.");
  return runPlaidLink(lt.link_token, itemId, lt.kind || kind);
}

// The last Plaid Link session that ended without connecting, for quoting to Plaid support.
let lastPlaidSession = null;
const plaidSessionLine = () => lastPlaidSession
  ? `Last Link attempt (${esc(lastPlaidSession.at)}): Link Session ID <code class="copyable">${esc(lastPlaidSession.sid)}</code>${
    lastPlaidSession.request ? ` · Request ID <code class="copyable">${esc(lastPlaidSession.request)}</code>` : ""}` : "";

// Runs Plaid Link. receivedRedirectUri: continuing after a bank's own sign-in page sent you back (OAuth).
function runPlaidLink(token, itemId, kind, receivedRedirectUri) {
  return new Promise((resolve) => {
    window.Plaid.create({
      token,
      ...(receivedRedirectUri ? { receivedRedirectUri } : {}),
      onSuccess: async (publicToken, metadata) => {
        try {
          toast(kind === "investments" ? "Connected. Pulling holdings and activity…" : "Connected. Reading accounts and statements…");
          const r = itemId
            ? await api(`/api/plaid/items/${encodeURIComponent(itemId)}/sync`, { method: "POST" })
            : await api("/api/plaid/exchange", { method: "POST", body: { public_token: publicToken, institution: metadata.institution } });
          toast(r.bank
            ? `Found ${r.accounts} account${r.accounts === 1 ? "" : "s"}` + (r.matched && r.matched.length ? ` · matched ${r.matched.join(", ")}` : "") +
              (r.statements ? ` · ${r.statements} card statement${r.statements === 1 ? "" : "s"}` : "")
            : `Synced ${r.accounts} account${r.accounts === 1 ? "" : "s"}, ${r.holdings} holdings, ${r.transactions} activities` +
              (r.hidden_simplefin && r.hidden_simplefin.length ? ` · hid the SimpleFIN copy of ${r.hidden_simplefin.join(", ")}` : ""));
        } catch (err) { toast(err.message, true); }
        resolve(true);
      },
      onExit: (err, metadata) => {
        // Shown so you can quote it to Plaid support ("Link Session ID"); also in the browser console.
        const sid = metadata && metadata.link_session_id;
        if (sid) console.info("Plaid Link session", sid, "request", metadata.request_id || "", "institution", metadata.institution?.name || "");
        if (err) toast(err.display_message || err.error_message || "Plaid closed", true);
        if (sid) {
          lastPlaidSession = { sid, request: metadata.request_id || "", at: new Date().toLocaleString() };
          const box = $("#pl-session");
          if (box) box.innerHTML = plaidSessionLine();
        }
        resolve(false);
      },
    }).open();
  });
}

// Back from a bank's sign-in page (/plaid/oauth?oauth_state_id=…): finish linking where it left off.
async function resumePlaidOAuth() {
  const back = location.href;
  history.replaceState(null, "", "/#setup/connections");
  route();
  try {
    const p = await api("/api/plaid/oauth_resume");
    await loadPlaid();
    if (await runPlaidLink(p.link_token, p.item_id, p.kind, back)) route();
  } catch (err) { toast(err.message, true); }
}

// A bank connection's accounts, each matched to one of yours (or added, or left out).
function plaidBankAccounts(it, accounts) {
  const mine = accounts.filter((a) => !a.id.startsWith("pl:") && ["checking", "savings", "credit", "loan"].includes(a.kind));
  return `<div class="pl-accts">${it.accounts.filter((p) => p.type !== "investment").map((p) => {
    const sel = p.ignored ? "ignore" : p.account_id || "";
    return `<div class="pl-acct" data-pid="${esc(p.id)}">
      <span class="acct-main"><span>${esc(p.name || p.official_name || "Account")}${p.mask ? ` <span class="muted">••${esc(p.mask)}</span>` : ""}</span>
        <span class="acct-sub">${nw(esc(p.subtype || p.type || ""))} · ${nw(fmt(p.balance))}</span></span>
      <select class="pl-match ${sel && sel !== "ignore" ? "ghost" : ""}" aria-label="Which of your accounts this is">
        <option value="" ${sel === "" ? "selected" : ""}>Choose…</option>
        ${p.account_id && p.account_id.startsWith("pl:") ? `<option value="${esc(p.account_id)}" selected>Its own account</option>` : `<option value="new">Add as a new account</option>`}
        ${mine.map((a) => `<option value="${esc(a.id)}" ${sel === a.id ? "selected" : ""}>Same as ${esc(a.display_name || a.name)}</option>`).join("")}
        <option value="ignore" ${sel === "ignore" ? "selected" : ""}>Don't use</option></select></div>`;
  }).join("")}</div>`;
}

// An investment connection's accounts: each is its own account, the same as one you have from SimpleFIN (so it's
// counted once), or left out. Runway decides when it's clear; the rest wait here with "Choose…".
function plaidInvestmentAccounts(it) {
  if (!it.accounts.length) return `<div class="acct-sub pl-inv">no accounts yet</div>`;
  const cands = it.candidates || [];
  return `<div class="pl-accts" data-inv>${it.accounts.map((p) => {
    const sel = p.account_id || "";
    return `<div class="pl-acct" data-pid="${esc(p.id)}">
      <span class="acct-main"><span>${esc(p.name || p.official_name || "Account")}${p.mask ? ` <span class="muted">••${esc(p.mask)}</span>` : ""}</span>
        <span class="acct-sub">${nw(esc(p.subtype || "investment"))} · ${nw(fmt(p.balance))}</span></span>
      <select class="pl-match ${sel && sel !== "ignore" ? "ghost" : ""}" aria-label="Which of your accounts this is">
        <option value="" ${sel === "" ? "selected" : ""}>Choose…</option>
        ${sel.startsWith("pl:") ? `<option value="new" selected>Its own account</option>` : `<option value="new">Add as a new account</option>`}
        ${cands.map((a) => `<option value="${esc(a.id)}" ${sel === a.id ? "selected" : ""}>Same as ${esc(a.display_name || a.name)} (${esc(fmt(a.balance))})</option>`).join("")}
        <option value="ignore" ${sel === "ignore" ? "selected" : ""}>Don't count it</option></select></div>`;
  }).join("")}</div>`;
}

// The logo for an institution by name (Plaid connections), or its first letter.
function bankIconFor(name) {
  const slug = brandFor(name || "");
  return slug ? `<img class="bank-icon" src="/banks/${slug}.svg" alt="" width="28" height="28">`
    : `<span class="bank-icon letter">${esc((name || "?").replace(/[^A-Za-z0-9]/g, "").slice(0, 1).toUpperCase() || "?")}</span>`;
}
// The same institution matching as runway/brands.py, for names the server hasn't matched to an account.
function brandFor(name) {
  const n = name.toLowerCase();
  const pats = [[/\bchase\b|jpmorgan/, "chase"], [/capital ?one/, "capital-one"], [/\bciti/, "citibank"], [/american express|\bamex\b/, "american-express"],
    [/\bdiscover\b/, "discover-card"], [/bank of america|\bbofa\b|merrill/, "bank-of-america"], [/wells ?fargo/, "wells-fargo"], [/\bu\.? ?s\.? bank\b/, "u-s-bank"],
    [/navy federal/, "navy-federal-credit-union"], [/\busaa\b/, "usaa"], [/fidelity/, "fidelity"], [/vanguard/, "vanguard"], [/schwab/, "charles-schwab"],
    [/e\*? ?trade/, "e-trade"], [/interactive brokers/, "interactive-brokers"], [/robinhood/, "robinhood"], [/\bsofi\b/, "sofi"], [/paypal/, "paypal"]];
  const hit = pats.find(([re]) => re.test(n));
  return hit ? hit[1] : null;
}

async function wirePlaidSetup() {
  const [st, accounts] = await Promise.all([api("/api/plaid/status"), api("/api/accounts")]);
  if (!$("#plaid-card")) return;
  $("#pl-env").value = st.env === "sandbox" ? "sandbox" : "production";
  $("#pl-keys").open = !st.configured;
  $("#pl-keys-sum").textContent = st.configured ? `Keys · saved · ${st.env === "sandbox" ? "Sandbox" : "Production"}` : "Keys";
  $("#pl-id").value = st.client_id;
  $("#pl-secret").placeholder = st.configured ? "•••••••• saved" : "secret";
  onEdit([$("#pl-env"), $("#pl-id")], async () => {
    await api("/api/plaid/settings", { method: "POST", body: { env: $("#pl-env").value, client_id: $("#pl-id").value } });
  });
  onEdit([$("#pl-secret")], async () => {
    if (!$("#pl-secret").value.trim()) return;
    await api("/api/plaid/settings", { method: "POST", body: { secret: $("#pl-secret").value } });
    toast("Plaid secret saved"); route();
  });
  if (st.redirect_uri) $("#pl-redirect").innerHTML = `In the Plaid Dashboard, add <code>${esc(st.redirect_uri)}</code> under Allowed redirect URIs (for banks like Chase that sign you in on their own site).`;
  const box = $("#pl-items");
  // Two connections to the same institution look alike, so each says when it was made (with the time, if there's a twin).
  const connectedOn = (it) => {
    const d = it.created_at ? new Date(it.created_at.replace(" ", "T") + "Z") : null;
    if (!d || isNaN(d)) return "";
    const twin = st.items.some((o) => o !== it && (o.institution_name || "") === (it.institution_name || ""));
    const opts = twin ? { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" } : { month: "short", day: "numeric", year: "numeric" };
    return `connected ${esc(d.toLocaleString("en-US", opts))} · `;
  };
  const duplicateNote = (it, items) => {
    const d = (it.duplicates || [])[0];
    if (!d) return "";
    const other = items.find((o) => o.item_id === d.item_id);
    const same = d.shared === it.accounts.length && d.shared === (other?.accounts.length ?? -1);
    return `<div class="pl-dup warn-text">${same ? `Same ${d.shared === 1 ? "account" : `${d.shared} accounts`} as the other ${esc(it.institution_name || "")} connection`
      : `${d.shared} of these accounts ${d.shared === 1 ? "is" : "are"} also in the other ${esc(it.institution_name || "")} connection`}, so ${d.shared === 1 ? "it's" : "they're"} counted twice. Remove one of the two.</div>`;
  };
  const synced = (t) => { if (!t) return "not synced"; const d = new Date(t.replace(" ", "T") + "Z");
    return isNaN(d) ? `synced ${esc(t)}` : `synced ${d.toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}`; };
  box.innerHTML = `${st.items.length ? `<div class="pl-list">${st.items.map((it) => `<div class="pl-item" data-item="${esc(it.item_id)}">
      <div class="pl-head">${bankIconFor(it.institution_name)}
        <span class="acct-main"><span class="acct-title">${esc(it.institution_name || "Connection")}
          ${it.env === "sandbox" ? `<span class="tag">sandbox</span>` : ""}<span class="tag">${it.bank ? (it.products.includes("transactions") ? "bank" : "card statements") : "investments"}</span></span>
          <span class="acct-sub">${connectedOn(it)}${it.error ? `<span class="warn-text">${esc(it.error === "ITEM_LOGIN_REQUIRED" ? "Login expired; reconnect to fix" : it.error)}</span>` : synced(it.last_sync)}</span></span>
        <span class="pl-btns">${it.error ? `<button class="btn primary pl-reconnect">Reconnect</button>` : `<button class="btn pl-sync">Sync</button>`}
          <button class="btn link pl-remove">Remove</button></span></div>
      ${duplicateNote(it, st.items)}
      ${it.bank ? plaidBankAccounts(it, accounts) : plaidInvestmentAccounts(it)}
    </div>`).join("")}</div>` : ""}
    <div class="form-row">${[["bank", "Connect a bank or card"], ["investments", "Connect an investment account"]].map(([k, label]) =>
      `<button class="btn ${k === "bank" ? "primary" : ""} pl-connect" data-kind="${k}" ${st.configured ? "" : "disabled title=\"Add your Plaid client ID and secret first\""}>${label}</button>`).join("")}</div>`;
  $$(".pl-connect").forEach((btn) => btn.addEventListener("click", async (e) => {
    const b = e.currentTarget; b.disabled = true;
    try { if (await openPlaidLink(null, b.dataset.kind)) route(); } catch (err) { toast(err.message, true); }
    b.disabled = false;
  }));
  $$(".pl-match").forEach((sel) => sel.addEventListener("change", async () => {
    try {
      await api("/api/plaid/match", { method: "POST", body: { plaid_account_id: sel.closest("[data-pid]").dataset.pid, target: sel.value } });
      const inv = !!sel.closest("[data-inv]");
      toast(sel.value === "new" ? "Added to your accounts" : sel.value === "ignore" ? "Left out" : !sel.value ? "Unmatched"
        : inv ? "Matched: it's counted once" : "Matched. Choose where its data comes from under Accounts.");
      await refreshState(); route();
    } catch (err) { toast(err.message, true); }
  }));
  $$("#pl-items [data-item]").forEach((tr) => {
    const id = tr.dataset.item;
    $(".pl-sync", tr)?.addEventListener("click", async (e) => {
      const b = e.currentTarget; b.disabled = true; b.textContent = "Syncing…";
      try {
        const r = await api(`/api/plaid/items/${encodeURIComponent(id)}/sync`, { method: "POST" });
        toast(r.bank ? `Synced · ${r.new_transactions} new transactions · ${r.statements} statements` : `Synced ${r.holdings} holdings, ${r.transactions} activities`);
      }
      catch (err) { toast(err.message, true); }
      route();
    });
    $(".pl-reconnect", tr)?.addEventListener("click", async () => { try { if (await openPlaidLink(id, "update")) route(); } catch (err) { toast(err.message, true); } });
    $(".pl-remove", tr).addEventListener("click", async (e) => {
      if (!confirmInline(e.currentTarget, "Remove this connection?")) return;
      try { await api(`/api/plaid/items/${encodeURIComponent(id)}/remove`, { method: "POST" }); toast("Connection removed"); route(); }
      catch (err) { toast(err.message, true); }
    });
  });
}

// ------------------------------------------------------------------------------------------ setup
let rulesOpen = true, rulesFilter = "";
// Owners: first names of the people who have signed in, plus "Joint".
function ownerOptions(sel) {
  const names = [...(STATE.owners || [])];
  if (sel && sel !== "Joint" && !names.includes(sel)) names.push(sel);
  return `<option value="">—</option>${names.map((n) => `<option ${n === sel ? "selected" : ""}>${esc(n)}</option>`).join("")}
    <option ${sel === "Joint" ? "selected" : ""}>Joint</option>`;
}
// ------------------------------------------------------------------------------------------ notifications
const b64uToBytes = (t) => Uint8Array.from(atob(t.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (t.length % 4)) % 4)), (c) => c.charCodeAt(0));
const isIOS = () => /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
const isInstalled = () => window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
function deviceName() {
  const ua = navigator.userAgent;
  const dev = /iPhone/.test(ua) ? "iPhone" : /iPad/.test(ua) || isIOS() ? "iPad" : /Android/.test(ua) ? "Android" : /Mac/.test(ua) ? "Mac" : /Windows/.test(ua) ? "Windows" : "Computer";
  const br = isInstalled() ? "app" : /Edg\//.test(ua) ? "Edge" : /Firefox\//.test(ua) ? "Firefox" : /Chrome\//.test(ua) ? "Chrome" : /Safari\//.test(ua) ? "Safari" : "browser";
  return `${dev} · ${br}`;
}

async function renderNotifications(box) {
  const d = await api("/api/push");
  const p = d.prefs;
  const supported = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
  let reg = null, sub = null;
  if (supported && window.isSecureContext) {
    try { reg = await navigator.serviceWorker.register("/sw.js"); sub = await reg.pushManager.getSubscription(); } catch (e) { console.warn(e); }
  }
  const mine = sub && d.devices.find((x) => x.endpoint === sub.endpoint);
  let status;
  if (!window.isSecureContext) {
    status = `<p>Notifications need Runway to be opened over <b>https://</b> (your <code>RUNWAY_PUBLIC_URL</code>). They can't be turned on from this address.</p>`;
  } else if (isIOS() && !isInstalled()) {
    status = `<p>On iPhone and iPad, notifications work once Runway is on your Home Screen (iOS 16.4 or later):</p>
      <ol class="steps"><li>Tap the <b>Share</b> button <span class="muted">(the square with an arrow)</span> in Safari.</li>
        <li>Choose <b>Add to Home Screen</b>, then <b>Add</b>.</li>
        <li>Open Runway from the new icon, come back to <b>Settings → Notifications</b> and turn them on.</li></ol>`;
  } else if (!supported) {
    status = `<p>This browser can't receive notifications. On iPhone, add Runway to the Home Screen from Safari; on a computer, use a current Chrome, Edge, Firefox or Safari.</p>`;
  } else if (Notification.permission === "denied") {
    status = `<p>Notifications are blocked for Runway on this device. ${isIOS() ? "Turn them on in the iPhone's <b>Settings → Notifications → Runway</b>" : "Allow them in your browser's site settings for this address"}, then come back here.</p>`;
  } else if (mine) {
    status = `<p><span class="sync-dot" style="display:inline-block;margin-right:6px"></span><b>On for this device</b> (${esc(mine.device)}).</p>
      <div class="form-row"><button class="btn primary" id="n-test">Send a test notification</button><button class="btn" id="n-off">Turn off on this device</button></div>`;
  } else {
    status = `<p>Get a notification on this ${isIOS() ? (/iPad/.test(navigator.userAgent) ? "iPad" : "iPhone") : "device"} when something needs your attention.</p>
      <div class="form-row"><button class="btn primary" id="n-on">Turn on notifications</button></div>`;
  }
  const toggle = (k, label, extra = "") => `<div class="notif-rule"><label class="inline"><input type="checkbox" class="n-pref" data-k="${k}" ${p[k] ? "checked" : ""}> ${label}</label>${extra}</div>`;
  const num = (k, pre, post, step) => `<label class="inline n-num">${pre}<input type="number" min="0" step="${step}" class="n-pref" data-k="${k}" value="${p[k]}">${post}</label>`;
  box.innerHTML = `<div class="card"><h2>This device</h2>${status}</div>
    <div class="card"><h2>What to tell you about</h2>
      
      ${toggle("card_due", "A card payment is coming up", num("card_due_days", "", " days ahead", 1))}
      ${toggle("low_balance", "The forecast gets low in the next 30 days", num("low_balance_below", "below $", "", 50))}
      ${toggle("missed", "A recurring payment didn't show up")}
      ${toggle("big_charge", "A large charge posts", num("big_charge_over", "over $", "", 50))}
      ${toggle("review", "Transactions are waiting for a category (at most once a day)")}
      ${toggle("sync_failed", "Syncing with your bank has been failing for a day")}
    </div>
    <div class="card"><h2>Devices</h2>
      ${d.devices.length ? `<table><tr><th>Device</th><th>Added</th><th>Last delivered</th><th></th></tr>${d.devices.map((x) => `<tr data-ep="${esc(x.endpoint)}">
        <td>${esc(x.device || "Device")}${sub && x.endpoint === sub.endpoint ? ` <span class="tag">this one</span>` : ""}
          ${x.last_error ? `<div class="desc" style="color:var(--critical)">${esc(x.last_error)}</div>` : ""}</td>
        <td class="muted">${x.created ? new Date(x.created * 1000).toLocaleDateString("en-US", { month: "short", day: "numeric" }) : ""}</td>
        <td class="muted">${x.last_ok ? new Date(x.last_ok * 1000).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "—"}</td>
        <td class="num"><button class="btn link n-remove">Remove</button></td></tr>`).join("")}</table>`
        : `<div class="empty">No devices yet. Turn notifications on from each phone or computer you want them on.</div>`}
    </div>
    ${d.recent.length ? `<div class="card"><h2>Recently sent</h2><table>${d.recent.map((r) => `<tr><td>${esc(r.title)}</td>
      <td class="num muted">${new Date(r.sent * 1000).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}</td></tr>`).join("")}</table></div>` : ""}`;

  $("#n-on")?.addEventListener("click", async (e) => {
    const b = e.currentTarget;
    b.disabled = true;
    try {
      // iOS only allows the permission prompt straight from a tap, so ask before anything else.
      const perm = await Notification.requestPermission();
      if (perm !== "granted") throw new Error(perm === "denied" ? "Notifications were blocked." : "Notifications weren't allowed.");
      reg = reg || (await navigator.serviceWorker.register("/sw.js"));
      await navigator.serviceWorker.ready;
      const s = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64uToBytes(d.public_key) });
      await api("/api/push/subscribe", { method: "POST", body: { subscription: s.toJSON(), device: deviceName() } });
      toast("Notifications are on");
      await api("/api/push/test", { method: "POST", body: { endpoint: s.endpoint } }).catch(() => {});
    } catch (err) { toast(err.message, true); }
    renderNotifications(box);
  });
  $("#n-test")?.addEventListener("click", async () => {
    try { await api("/api/push/test", { method: "POST", body: { endpoint: sub.endpoint } }); toast("Sent. It should arrive in a few seconds."); }
    catch (err) { toast(err.message, true); }
    renderNotifications(box);
  });
  $("#n-off")?.addEventListener("click", async () => {
    try { await api("/api/push/unsubscribe", { method: "POST", body: { endpoint: sub.endpoint } }); await sub.unsubscribe(); toast("Notifications are off on this device"); }
    catch (err) { toast(err.message, true); }
    renderNotifications(box);
  });
  $$(".n-remove", box).forEach((b) => b.addEventListener("click", async (e) => {
    if (!confirmInline(e.currentTarget, "Remove?")) return;
    const ep = b.closest("tr").dataset.ep;
    await api("/api/push/unsubscribe", { method: "POST", body: { endpoint: ep } });
    if (sub && sub.endpoint === ep) await sub.unsubscribe().catch(() => {});
    renderNotifications(box);
  }));
  onEdit($$(".n-pref", box), async (f) => {
    await api("/api/push/prefs", { method: "POST", body: { [f.dataset.k]: f.type === "checkbox" ? f.checked : f.value } });
  });
}

let SETUP_SECTION = "";
async function renderSetup(el, sub) {
  SETUP_SECTION = sub || SETUP_SECTION;
  await loadCategories();
  const [accounts, recurring, rules] = await Promise.all([api("/api/accounts"), api("/api/recurring"), api("/api/rules")]);
  const cash = accounts.filter((a) => a.kind === "checking" || a.kind === "savings");
  const name = (a) => a.display_name || a.name;
  const acctName = (id) => { const a = accounts.find((x) => x.id === id); return a ? name(a) : "?"; };
  const acctOptions = (list, sel) => list.map((a) => `<option value="${esc(a.id)}" ${a.id === sel ? "selected" : ""}>${esc(name(a))}</option>`).join("");

  const sections = {
    accounts: { label: "Accounts", html: () => `
  <div class="card"><h2>Forecast</h2>
    <div class="form-row"><label>Primary account (the one the forecast shows)
      <select id="primary-acct">${cash.length > 1 || !STATE.primary_account ? `<option value="">Choose…</option>` : ""}${acctOptions(cash, STATE.primary_account || (cash.filter((a) => a.kind === "checking").length === 1 ? cash.find((a) => a.kind === "checking").id : ""))}</select></label>
      <label>Forecast length (days)<input id="horizon-days" type="number" min="14" max="365" value="${STATE.horizon_days}"></label></div>
  </div>
  ${STATE.plaid_undecided ? `<div class="warn"><span class="icon">!</span><span>${STATE.plaid_undecided === 1 ? "An account" : `${STATE.plaid_undecided} accounts`}
    from Plaid ${STATE.plaid_undecided === 1 ? "is" : "are"} waiting for you to say what ${STATE.plaid_undecided === 1 ? "it is" : "they are"}, so ${STATE.plaid_undecided === 1 ? "it isn't" : "they aren't"}
    listed here or counted in net worth yet. <a href="#setup/connections">Connections</a></span></div>` : ""}
  <div class="card"><h2>Accounts</h2>
    ${accounts.length ? accountGroups(accounts, cash)
    : `<div class="empty">Accounts appear here after the first sync.</div>`}
  </div>` },
    categories: { label: "Categories", html: () => `<div class="card"><div class="card-head"><h2>Categories</h2>
      <button class="btn" id="cat-new-btn">Add</button></div>
    <div class="acct-form" id="cat-new" hidden><div class="form-row"><label>Name<input id="cat-new-name"></label>
      <label>Subcategory of<select id="cat-new-parent"><option value="">— none (top level) —</option>${categoryOptions("", { blank: false, canHoldChildren: true })}</select></label>
      <label class="inline"><input type="checkbox" id="cat-transfer"> Not spending (a transfer)</label>
      <label class="inline"><input type="checkbox" id="cat-income"> Money in</label>
      <button class="btn primary" id="cat-add">Add</button></div></div>
    <div id="cat-table" class="tidy-list">
      ${CATEGORIES.map((c) => `<div data-name="${esc(c.name)}" class="tidy-row ${c.parent ? "sub" : ""}" style="--depth:${c.depth || 0}">
        <span class="tidy-main">${c.protected ? `<span class="tidy-name">${esc(c.name)}</span>`
          : `<input class="cat-name ghost" value="${esc(c.name)}" aria-label="Category name">`}
          ${c.is_transfer ? `<span class="tag">not spending</span>` : c.is_income ? `<span class="tag">money in</span>` : ""}${c.protected ? `<span class="tag">built-in</span>` : ""}</span>
        <span class="tidy-count" title="Transactions">${c.transactions || ""}</span>
        <span class="cat-actions tidy-actions">${(c.depth || 0) < CAT_MAX_DEPTH - 1 ? `<button class="btn link cat-sub">+ Sub</button>` : ""}
          ${c.protected ? "" : `<button class="btn link cat-move">Move</button><button class="btn link cat-del">Remove</button>`}</span></div>`).join("")}
    </div>
  </div>` },
    rules: { label: "Rules", count: rules.length, html: () => `<div class="card"><div class="card-head"><h2>Rules</h2>
      <button class="btn" id="rule-new-btn">Add</button></div>
    <p class="help">When a transaction matches everything a rule asks for, the rule acts on it. Rules run on new transactions as
      they sync; <b>Apply</b> runs one over past ones too (it won't change a category you picked yourself). The most specific rule wins.</p>
    <div id="rule-editor-new"></div>
    ${rules.length ? `<input id="rule-filter" class="tidy-search" placeholder="Search ${rules.length} rules" value="${esc(rulesFilter)}">
      <div id="rules-table" class="tidy-list">${rules.map((r) => `<div class="rule-item" data-id="${r.id}" data-q="${esc((r.summary + " " + ruleActions(r)).toLowerCase())}">
        <div class="tidy-row rule-row">
          <span class="tidy-main rule-when">${esc(r.summary || "any transaction")}</span>
          <span class="rule-arrow" aria-hidden="true">→</span>
          <span class="rule-then">${esc(ruleActions(r))}</span>
          <span class="tidy-actions"><button class="btn link rule-edit">Edit</button>
            <button class="btn link rule-apply" title="Run this rule over past transactions (not ones you categorized yourself)">Apply</button>
            <button class="btn link del-rule">Remove</button></span></div>
        <div class="rule-editor-slot"></div></div>`).join("")}</div>` : ""}
  </div>` },
    connections: { label: "Connections", html: () => `
  <div class="card"><h2>Bank connection <span class="muted small">SimpleFIN</span></h2>
    ${STATE.simplefin
      ? `<p>Connected.</p>
         ${STATE.last_log ? `<p class="small muted">Last sync: ${esc(STATE.last_log.at)} UTC — ${esc(STATE.last_log.message)}</p>` : ""}
         <details><summary class="small">Replace the connection</summary>${connectForm()}</details>`
      : `<p class="help">Paste a setup token from <a href="https://beta-bridge.simplefin.org" target="_blank" rel="noopener">SimpleFIN Bridge</a>.</p>${connectForm()}`}
  </div>
<div class="card" id="plaid-card"><h2>Plaid</h2>
    <div id="pl-items"></div>
    <details id="pl-keys" class="pl-keys"><summary class="small"><span id="pl-keys-sum">Keys</span></summary>
      <p class="help">From Developers → Keys at <a href="https://dashboard.plaid.com" target="_blank" rel="noopener">dashboard.plaid.com</a>.</p>
      <div class="form-row">
        <label>Environment<select id="pl-env"><option value="production">Production (your real accounts)</option><option value="sandbox">Sandbox (test data)</option></select></label>
        <label>Client ID<input id="pl-id" style="width:220px" autocomplete="off" spellcheck="false"></label>
        <label>Secret<input id="pl-secret" type="password" style="width:220px" autocomplete="off"></label>
      </div>
      <p class="help" id="pl-redirect"></p>
    </details>
    <p class="help" id="pl-session">${plaidSessionLine()}</p>
  </div>
<div class="card" id="retail-card"><h2>Amazon and Target orders</h2><div class="muted">Loading…</div></div>
<div class="card"><h2>AI categorization <span class="muted small">optional, via OpenRouter</span></h2>
    <p class="help">Only the date, amount, merchant and account type of each transaction are sent.</p>
    <div class="form-row">
      <label>OpenRouter API key<input id="api-key" type="password" placeholder="${STATE.has_api_key ? "•••••••• saved" : "sk-or-…"}" style="width:260px" autocomplete="off"></label>
      <label>Model<input id="llm-model" value="${esc(STATE.llm_model)}" style="width:240px" spellcheck="false"></label>
      ${STATE.has_api_key ? `<button class="btn" id="clear-key">Remove key</button>` : ""}
    </div>
    <label class="inline"><input type="checkbox" id="auto-ai" ${STATE.auto_ai_on_sync ? "checked" : ""}>
      Categorize new merchants during each sync when the AI is confident</label>
    ${STATE.last_llm_error ? `<div class="warn critical" style="margin-top:10px"><span class="icon">!</span><span>Last AI error: ${esc(STATE.last_llm_error)}</span></div>` : ""}
  </div>
<div class="card"><h2>Home values <span class="muted small">optional, via RentCast</span></h2>
    <p class="help">A free <a href="https://app.rentcast.io/app/api" target="_blank" rel="noopener">RentCast key</a> keeps home values current (Runway stays within the 50 free lookups a month).</p>
    <div class="form-row"><label>RentCast API key<input id="rc-key" type="password" style="width:280px" autocomplete="off" placeholder="${STATE.rentcast_configured ? "•••••••• saved" : "paste your key"}"></label>
${STATE.rentcast_configured ? `<button class="btn link" id="rc-clear">Remove key</button>` : ""}</div>
  </div>` },
    notifications: { label: "Notifications", html: () => `<div id="notif-box"><div class="card empty">Loading…</div></div>` },
    backup: { label: "Backup", html: () => `<div class="card"><h2>Backup &amp; restore</h2>
    <p class="help">Everything, including bank access and API keys: keep it private. Database: ${STATE.database === "postgres" ? "Postgres" : "SQLite"}.</p>
    <div class="form-row"><a class="btn primary" href="/api/backup" download>Download a backup</a></div>
    <div class="form-row">
      <label>Restore from a backup<input type="file" id="restore-file" accept=".gz,.json,application/gzip,application/json"></label>
      <button class="btn" id="restore-go" disabled>Restore…</button>
    </div>
  </div>` },
  };
  const section = sections[SETUP_SECTION] ? SETUP_SECTION : (STATE.connected ? "accounts" : "connections");
  el.innerHTML = `<h1>Settings</h1>
  <div class="subtabs" role="tablist">${Object.entries(sections).map(([k, v]) =>
    `<a href="#setup/${k}" role="tab" class="${k === section ? "active" : ""}" aria-selected="${k === section}">${v.label}${v.count ? ` <span class="muted small">${v.count}</span>` : ""}</a>`).join("")}</div>
  ${sections[section].html()}
  <p class="small muted version-line">Runway ${esc(STATE.version && STATE.version !== "dev" ? STATE.version : "development build")}${STATE.version && STATE.version !== "dev"
    ? ` · <a href="https://github.com/AnthonyPluth/Runway/releases/tag/${encodeURIComponent(STATE.version)}" target="_blank" rel="noopener">what's new</a>` : ""}</p>`;

  wireConnect();
  if (section === "notifications") renderNotifications($("#notif-box"));
  $("#restore-file")?.addEventListener("change", (e) => { $("#restore-go").disabled = !e.target.files.length; });
  $("#restore-go")?.addEventListener("click", async (e) => {
    const file = $("#restore-file").files[0];
    if (!file) return;
    if (!confirmInline(e.currentTarget, "Replace everything here with this backup?")) return;
    const b = e.currentTarget; b.disabled = true; b.textContent = "Restoring…";
    try {
      const res = await fetch("/api/restore", { method: "POST", headers: { "X-Runway": "1", "Content-Type": "application/octet-stream" }, body: file });
      const r = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(r.error || `Restore failed (${res.status})`);
      toast(`Restored backup from ${new Date(r.created).toLocaleString()} · ${r.transactions} transactions, ${r.accounts} accounts`);
      await refreshState(); route();
    } catch (err) { toast(err.message, true); b.disabled = false; b.textContent = "Restore…"; }
  });
  if ($("#plaid-card")) wirePlaidSetup();
  if ($("#retail-card")) renderRetailCard($("#retail-card"));
  $("#rules-box")?.addEventListener("toggle", (e) => { rulesOpen = e.currentTarget.open; });
  const filterRules = () => {
    const q = ($("#rule-filter")?.value || "").trim().toLowerCase();
    rulesFilter = q;
    $$("#rules-table [data-id]").forEach((tr) => { tr.hidden = !!q && !tr.dataset.q.includes(q); });
  };
  $("#rule-filter")?.addEventListener("input", filterRules);
  filterRules();
  onEdit([$("#rc-key")], async () => {
    if (!$("#rc-key").value.trim()) return;
    await api("/api/rentcast/settings", { method: "POST", body: { api_key: $("#rc-key").value } }); toast("RentCast key saved"); await refreshState(); route();
  });
  $("#rc-clear")?.addEventListener("click", async () => { await api("/api/rentcast/settings", { method: "POST", body: { clear: true } }); await refreshState(); route(); });
  $("#primary-acct")?.addEventListener("change", async (e) => {
    try { await api("/api/settings", { method: "POST", body: { primary_account: e.target.value } }); toast("Primary account saved"); await refreshState(); }
    catch (err) { toast(err.message, true); }
  });
  $$(".acct-row[data-id]").forEach((tr) => {
    tr.addEventListener("toggle", () => { if (tr.open) openAccounts.add(tr.dataset.id); else openAccounts.delete(tr.dataset.id); });
    $(".f-kind", tr).addEventListener("change", () => saveAccount(tr, true));
    $(".type-change", tr).addEventListener("click", (e) => { e.currentTarget.hidden = true; $(".f-kind", tr).hidden = false; $(".f-kind", tr).focus(); });
    $(".f-provider", tr)?.addEventListener("change", async (e) => {
      try {
        await api(`/api/accounts/${encodeURIComponent(tr.dataset.id)}`, { method: "POST", body: { provider: e.target.value } });
        toast(e.target.value === "plaid" ? "This account now comes from Plaid; its transactions arrive with the next sync" : "Back to SimpleFIN");
        if (e.target.value === "plaid") api("/api/sync", { method: "POST" }).then(() => route(), () => {});
      } catch (err) { toast(err.message, true); route(); }
    });
    onEdit($$(".f-name, .f-sign, .f-spend", tr), () => saveAccount(tr, false));
    onEdit($$(".f-owner, .f-payfrom, .f-hidden", tr), () => saveAccount(tr, true));   // these change the row's summary
    $(".f-name", tr).addEventListener("input", (e) => { $(".acct-title", tr).textContent = e.target.value.trim() || e.target.placeholder; });
  });
  onEdit([$("#llm-model")], async () => { await api("/api/settings", { method: "POST", body: { llm_model: $("#llm-model").value.trim() } }); await refreshState(); });
  onEdit([$("#api-key")], async () => {
    const v = $("#api-key").value.trim();
    if (!v) return;
    await api("/api/settings", { method: "POST", body: { openrouter_api_key: v } });
    toast("API key saved"); await refreshState(); route();
  });
  $("#clear-key")?.addEventListener("click", async () => { await api("/api/settings", { method: "POST", body: { openrouter_api_key: "" } }); await refreshState(); route(); });
  $("#auto-ai")?.addEventListener("change", async (e) => {
    await api("/api/settings", { method: "POST", body: { auto_ai_on_sync: e.target.checked } }); toast("Saved"); refreshState();
  });
  const byRule = Object.fromEntries(rules.map((r) => [String(r.id), r]));
  $("#rule-new-btn")?.addEventListener("click", () => openRuleEditor($("#rule-editor-new"), null, accounts));
  if (!rules.length && $("#rule-editor-new")) openRuleEditor($("#rule-editor-new"), null, accounts);
  $$("#rules-table [data-id]").forEach((item) => {
    const id = item.dataset.id;
    $(".rule-edit", item).addEventListener("click", () => openRuleEditor($(".rule-editor-slot", item), byRule[id], accounts));
    $(".rule-apply", item).addEventListener("click", async () => {
      try { const r = await api(`/api/rules/${id}/apply`, { method: "POST" }); toast(`${r.updated} transaction${r.updated === 1 ? "" : "s"} updated`); refreshState(); }
      catch (err) { toast(err.message, true); }
    });
    $(".del-rule", item).addEventListener("click", async (e) => {
      if (!confirmInline(e.currentTarget, "Remove?")) return;
      await api(`/api/rules/${id}`, { method: "DELETE" }); toast("Rule removed"); route();
    });
  });
  const addCategory = async (name, parent, isTransfer, isIncome) => {
    try { await api("/api/categories", { method: "POST", body: { name, parent, is_transfer: isTransfer, is_income: isIncome } });
      toast(parent ? `Added ${name} under ${parent}` : `Added ${name}`); route(); }
    catch (err) { toast(err.message, true); }
  };
  $("#cat-new-parent")?.addEventListener("change", (e) => {  // a subcategory takes its parent's kind
    $("#cat-transfer").disabled = $("#cat-income").disabled = !!e.target.value;
  });
  $("#cat-add")?.addEventListener("click", () => addCategory($("#cat-new-name").value, $("#cat-new-parent").value || null,
    $("#cat-transfer").checked, $("#cat-income").checked));
  $("#cat-new-btn")?.addEventListener("click", () => { $("#cat-new").hidden = false; $("#cat-new-name").focus(); });
  $$("#cat-table [data-name]").forEach((tr) => {
    const name = tr.dataset.name;
    $(".cat-name", tr)?.addEventListener("change", async (e) => {
      try { await api("/api/categories/rename", { method: "POST", body: { name, new_name: e.target.value } }); toast("Renamed"); route(); }
      catch (err) { toast(err.message, true); e.target.value = name; }
    });
    $(".cat-sub", tr)?.addEventListener("click", () => {
      const cell = $(".cat-actions", tr);
      cell.innerHTML = `<input class="sub-name" placeholder="New subcategory of ${esc(name)}" style="width:220px"> <button class="btn primary sub-ok">Add</button> <button class="btn link sub-cancel">Cancel</button>`;
      $(".sub-name", cell).focus();
      const go = () => addCategory($(".sub-name", cell).value, name, false, false);
      $(".sub-ok", cell).addEventListener("click", go);
      $(".sub-name", cell).addEventListener("keydown", (e) => { if (e.key === "Enter") go(); if (e.key === "Escape") route(); });
      $(".sub-cancel", cell).addEventListener("click", () => route());
    });
    $(".cat-move", tr)?.addEventListener("click", () => {
      const c = CATEGORIES.find((x) => x.name === name);
      // How many levels this branch needs, so we only offer parents it fits under.
      const height = 1 + Math.max(0, ...CATEGORIES.filter((x) => x.path.includes(name)).map((x) => x.path.length - c.path.length));
      const options = categoryOptions(c.parent || "", {
        blank: false,
        exclude: (x) => x.path.includes(name) || x.path.length + height > CAT_MAX_DEPTH,
      });
      const cell = $(".cat-actions", tr);
      cell.innerHTML = `<select class="move-parent" aria-label="Move ${esc(name)} under"><option value="" ${c.parent ? "" : "selected"}>Top level</option>${options}</select>
        <button class="btn primary move-ok">Move</button> <button class="btn link move-cancel">Cancel</button>`;
      $(".move-cancel", cell).addEventListener("click", () => route());
      $(".move-ok", cell).addEventListener("click", async () => {
        const parent = $(".move-parent", cell).value || null;
        try {
          await api("/api/categories/move", { method: "POST", body: { name, parent } });
          toast(parent ? `Moved ${name} under ${parent}` : `${name} is now a top-level category`);
          await refreshState(); route();
        } catch (err) { toast(err.message, true); }
      });
    });
    $(".cat-del", tr)?.addEventListener("click", () => {
      const c = CATEGORIES.find((x) => x.name === name);
      const cell = $(".cat-actions", tr);
      const others = categoryOptions("", { blank: false }).replace(`<option value="${esc(name)}" `, `<option disabled value="${esc(name)}" `);
      cell.innerHTML = `${c.transactions ? `<select class="move-to" aria-label="Move transactions to"><option value="">Send its ${c.transactions} transaction${c.transactions === 1 ? "" : "s"} to Review</option>
          <optgroup label="Or move them to">${others.replace(/<optgroup[^>]*>|<\/optgroup>/g, "")}</optgroup></select> ` : ""}
        <button class="btn primary del-ok">Remove ${esc(name)}</button> <button class="btn link del-cancel">Cancel</button>`;
      $(".del-cancel", cell).addEventListener("click", () => route());
      $(".del-ok", cell).addEventListener("click", async () => {
        try {
          const r = await api("/api/categories/remove", { method: "POST", body: { name, move_to: $(".move-to", cell)?.value || null } });
          toast(r.moved ? `Removed · ${r.moved} transaction${r.moved === 1 ? "" : "s"} ${$(".move-to", cell)?.value ? "moved" : "sent to Review"}` : "Removed");
          await refreshState(); route();
        } catch (err) { toast(err.message, true); }
      });
    });
  });
  onEdit([$("#horizon-days")], async () => {
    await api("/api/settings", { method: "POST", body: { horizon_days: Number($("#horizon-days").value) } });
    horizon = null; await refreshState();
  });
}

// What a rule does, in a few words: "Restaurants · rename to Chipotle · review".
function ruleActions(r) {
  const bits = [];
  if (r.split) bits.push("split " + r.split.map((p) => `${p.percent}% ${p.category}`).join(", "));
  if (r.category) bits.push(r.category);
  if (r.rename) bits.push(`rename to ${r.rename}`);
  if (r.review) bits.push("put in Review");
  return bits.join(" · ");
}

// Add or edit a rule: conditions on the left, what it does on the right, and a live count of what it would match.
function openRuleEditor(slot, r, accounts) {
  if (slot.firstChild) { slot.innerHTML = ""; return; }
  r = r || { match: "", match_mode: "contains", category: "", review: 0 };
  const splitParts = r.split ? r.split.map((p) => ({ ...p })) : [];
  slot.innerHTML = `<div class="rule-editor">
    <div class="rule-cols">
      <fieldset><legend>When a transaction</legend>
        <div class="form-row"><label>Merchant or description
            <span class="rule-text"><select class="re-mode" aria-label="How the text matches">
              ${[["contains", "contains"], ["exact", "is exactly"], ["starts", "starts with"]].map(([v, l]) =>
                `<option value="${v}" ${r.match_mode === v ? "selected" : ""}>${l}</option>`).join("")}</select>
            <input class="re-match" value="${esc(r.match || "")}" placeholder="whole foods" spellcheck="false"></span></label></div>
        <div class="form-row"><label>Amount from<input class="re-min num" type="number" min="0" step="0.01" inputmode="decimal" value="${r.amount_min ?? ""}" placeholder="any"></label>
          <label>to<input class="re-max num" type="number" min="0" step="0.01" inputmode="decimal" value="${r.amount_max ?? ""}" placeholder="any"></label>
          <label>Direction<select class="re-dir"><option value="">Either</option>
            <option value="out" ${r.direction === "out" ? "selected" : ""}>Money out</option>
            <option value="in" ${r.direction === "in" ? "selected" : ""}>Money in</option></select></label></div>
        <div class="form-row"><label>Account<select class="re-acct"><option value="">Any account</option>
          ${accounts.map((a) => `<option value="${esc(a.id)}" ${a.id === r.account_id ? "selected" : ""}>${esc(a.display_name || a.name)}</option>`).join("")}</select></label></div>
      </fieldset>
      <fieldset><legend>Then</legend>
        <div class="form-row"><label>Category<select class="re-cat" ${splitParts.length ? "disabled" : ""}>
            <option value="">Leave it (other rules, history or AI decide)</option>${categoryOptions(r.category || "", { blank: false })}</select></label>
          <button class="btn link re-split-toggle">${splitParts.length ? "Don't split" : "Split instead…"}</button></div>
        <div class="re-split"></div>
        <div class="form-row"><label>Rename the merchant to<input class="re-rename" value="${esc(r.rename || "")}" placeholder="keep as is"></label></div>
        <label class="inline"><input type="checkbox" class="re-review" ${r.review ? "checked" : ""}> Put it in Review so I look at it</label>
      </fieldset>
    </div>
    <div class="re-preview muted small"></div>
    <div class="re-examples"></div>
    <div class="form-row re-foot">
      <label class="inline"><input type="checkbox" class="re-apply" ${r.id ? "" : "checked"}> Apply to past transactions</label>
      <span class="split-actions"><button class="btn re-cancel">Cancel</button><button class="btn primary re-save">${r.id ? "Save rule" : "Add rule"}</button></span>
    </div></div>`;
  const q = (sel) => $(sel, slot);
  const drawSplit = () => {
    const box = q(".re-split");
    q(".re-cat").disabled = splitParts.length > 0;
    q(".re-split-toggle").textContent = splitParts.length ? "Don't split" : "Split instead…";
    if (!splitParts.length) { box.innerHTML = ""; return; }
    const total = splitParts.reduce((n, p) => n + (parseFloat(p.percent) || 0), 0);
    box.innerHTML = `${splitParts.map((p, i) => `<div class="split-row" data-i="${i}">
        <select class="rs-cat" aria-label="Category">${categoryOptions(p.category || "")}</select>
        <input class="rs-pct num" type="number" min="0" max="100" step="0.01" value="${esc(p.percent ?? "")}" aria-label="Percent"> %
        <button class="btn link rs-drop" aria-label="Remove this part">✕</button></div>`).join("")}
      <div class="split-foot"><button class="btn link rs-add">+ Add a part</button>
        <span class="split-left ${Math.abs(total - 100) > 0.01 ? "over" : "muted"}">${Math.abs(total - 100) > 0.01 ? `${Math.round(total * 100) / 100}% of 100%` : "adds up"}</span></div>`;
    $$(".split-row", box).forEach((row) => {
      const p = splitParts[row.dataset.i];
      $(".rs-cat", row).addEventListener("change", (e) => { p.category = e.target.value; refresh(); });
      $(".rs-pct", row).addEventListener("change", (e) => { p.percent = e.target.value; drawSplit(); refresh(); });
      $(".rs-drop", row).addEventListener("click", () => { splitParts.splice(row.dataset.i, 1); if (splitParts.length === 1) splitParts.length = 0; drawSplit(); refresh(); });
    });
    $(".rs-add", box).addEventListener("click", () => {
      const left = 100 - splitParts.reduce((n, p) => n + (parseFloat(p.percent) || 0), 0);
      splitParts.push({ category: "", percent: left > 0 ? Math.round(left * 100) / 100 : "" }); drawSplit();
    });
  };
  const body = () => ({
    match: q(".re-match").value, match_mode: q(".re-mode").value,
    amount_min: q(".re-min").value, amount_max: q(".re-max").value, direction: q(".re-dir").value,
    account_id: q(".re-acct").value, category: splitParts.length ? "" : q(".re-cat").value,
    rename: q(".re-rename").value, review: q(".re-review").checked,
    split: splitParts.length ? splitParts.map((p) => ({ category: p.category, percent: parseFloat(p.percent) })) : null,
  });
  let timer, seq = 0;
  const refresh = () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const mine = ++seq;
      const p = await api("/api/rules/preview", { method: "POST", body: body() }).catch((e) => ({ error: e.message }));
      if (mine !== seq || !slot.isConnected) return;
      q(".re-preview").textContent = p.error ? p.error
        : `Matches ${p.matches} past transaction${p.matches === 1 ? "" : "s"}${p.matches ? ` · applying it would change ${p.changes}` : ""}`;
      q(".re-examples").innerHTML = p.examples?.length ? `<table class="small">${p.examples.map((t) => `<tr>
          <td class="muted">${fmtDate(t.posted)}</td><td>${esc(t.payee || t.description)}</td><td class="muted hide-sm">${esc(t.account_name)}</td>
          <td class="num">${fmt(t.amount)}</td><td class="muted">${esc(t.category || "—")}</td></tr>`).join("")}</table>` : "";
    }, 250);
  };
  q(".re-split-toggle").addEventListener("click", () => {
    if (splitParts.length) splitParts.length = 0;
    else splitParts.push({ category: q(".re-cat").value || "", percent: 50 }, { category: "", percent: 50 });
    drawSplit(); refresh();
  });
  $$("input, select", slot).forEach((el) => el.addEventListener(el.type === "checkbox" || el.tagName === "SELECT" ? "change" : "input", refresh));
  q(".re-cancel").addEventListener("click", () => { slot.innerHTML = ""; });
  q(".re-save").addEventListener("click", async (e) => {
    const b = e.currentTarget; b.disabled = true;
    try {
      let updated = 0;
      if (r.id) {
        await api(`/api/rules/${r.id}`, { method: "POST", body: body() });
        if (q(".re-apply").checked) updated = (await api(`/api/rules/${r.id}/apply`, { method: "POST" })).updated;
      } else {
        updated = (await api("/api/rules", { method: "POST", body: { ...body(), apply: q(".re-apply").checked } })).updated;
      }
      toast(`${r.id ? "Rule saved" : "Rule added"}${updated ? ` · ${updated} transaction${updated === 1 ? "" : "s"} updated` : ""}`);
      refreshState(); route();
    } catch (err) { toast(err.message, true); b.disabled = false; }
  });
  drawSplit();
  refresh();
  q(".re-match").focus();
}

function connectForm() {
  return `<div class="form-row" style="flex-direction:column;align-items:stretch">
    <textarea id="sf-token" placeholder="Paste a SimpleFIN setup token (or an access URL, if you already claimed one)" autocomplete="off" spellcheck="false"></textarea>
    <div><button class="btn primary" id="sf-connect">Connect and sync</button></div></div>`;
}
function wireConnect() {
  $("#sf-connect")?.addEventListener("click", async (e) => {
    const b = e.currentTarget;
    b.disabled = true; b.textContent = "Connecting…";
    try {
      await api("/api/connect", { method: "POST", body: { token: $("#sf-token").value } });
      b.textContent = "Syncing (first sync pulls ~6 months)…";
      const r = await api("/api/sync", { method: "POST" });
      toast(`Connected · ${r.new} transactions imported`);
      await refreshState(); route();
    } catch (err) { toast(err.message, true); b.disabled = false; b.textContent = "Connect and sync"; }
  });
}

// Settings → Accounts: one compact row per account, grouped by type; click a row to edit it.
const openAccounts = new Set();
const KIND_GROUPS = [["Cash", ["checking", "savings"]], ["Credit cards", ["credit"]], ["Loans", ["loan"]], ["Investments", ["investment"]]];

function accountGroups(accounts, cash) {
  const shown = accounts.filter((a) => !a.hidden), hidden = accounts.filter((a) => a.hidden);
  const byName = Object.fromEntries(accounts.map((a) => [a.id, a.display_name || a.name]));
  const section = (title, list) => list.length ? `<div class="acct-group"><div class="acct-group-title">${esc(title)}</div>
      ${list.map((a) => accountRow(a, cash, byName)).join("")}</div>` : "";
  return KIND_GROUPS.map(([title, kinds]) => section(title, shown.filter((a) => kinds.includes(a.kind)))).join("")
    + section("Hidden", hidden);
}

// Why a linked card has no statement yet (Plaid's error code, if it gave one).
function statementNote(code) {
  return ({ ADDITIONAL_CONSENT_REQUIRED: "Plaid needs your consent to share card statements: reconnect this bank in Settings → Connections.",
    PRODUCTS_NOT_SUPPORTED: "This bank doesn't share card statements through Plaid.", INSTITUTION_NOT_SUPPORTED: "This bank doesn't share card statements through Plaid.",
    INVALID_PRODUCT: "Card statements (Liabilities) aren't enabled for your Plaid account.", PRODUCTS_NOT_ENABLED: "Card statements (Liabilities) aren't enabled for your Plaid account.",
    PRODUCT_NOT_READY: "Plaid is still gathering the statement; it usually arrives with the next sync.",
    NO_LIABILITY_ACCOUNTS: "Plaid didn't find card statements at this bank." })[code] || "It usually arrives with the next sync.";
}

function accountSummary(a, byName) {
  const bits = [];
  if (a.id === STATE.primary_account) bits.push(`<span class="tag">primary</span>`);
  if (a.owner) bits.push(esc(a.owner));
  if (a.kind === "credit") {
    bits.push(a.pay_from ? `paid from ${esc(byName[a.pay_from] || "?")}` : `<span class="warn-text">no paying account</span>`);
    if (!a.plaid_link) bits.push(`<span class="warn-text">not linked through Plaid</span>`);
    else if (!a.plaid_link.closed) bits.push(`<span class="warn-text" title="${esc(statementNote(a.plaid_link.statement_note))}">no statement from ${esc(a.plaid_link.institution || "the bank")} yet</span>`);
  }
  if (a.provider === "plaid" || a.id.startsWith("pl:")) bits.push("via Plaid");
  return bits.map(nw).join(" · ");
}

function accountRow(a, cash, byName) {
  const bank = `${a.org && !a.name.toLowerCase().includes(a.org.toLowerCase()) ? a.org + " " : ""}${a.name}`;
  const owes = a.kind === "credit" || a.kind === "loan";
  return `<details class="acct-row" data-id="${esc(a.id)}" ${openAccounts.has(a.id) ? "open" : ""}>
    <summary>${acctIcon(a.id) || `<span class="bank-icon letter">?</span>`}
      <span class="acct-main"><span class="acct-title">${esc(a.display_name || a.name)}</span>
        <span class="acct-sub">${accountSummary(a, byName)}</span></span>
      <span class="acct-bal ${a.balance < 0 ? "" : ""}">${fmt(a.balance)}</span>
      <span class="acct-chev" aria-hidden="true">›</span></summary>
    <div class="acct-edit">
      <label class="wide">Name<input class="f-name" value="${esc(a.display_name || "")}" placeholder="${esc(a.name)}">
        ${a.display_name ? `<span class="field-note" title="${esc(bank)}">From the bank: ${esc(bank)}</span>` : ""}</label>
      <label>Owner<select class="f-owner">${ownerOptions(a.owner)}</select></label>
      ${a.kind === "credit" ? `<label>Paid from<select class="f-payfrom"><option value="">—</option>${cash.map((c) => `<option value="${esc(c.id)}" ${c.id === a.pay_from ? "selected" : ""}>${esc(c.display_name || c.name)}</option>`).join("")}</select></label>` : ""}
      ${providerControl(a)}
      <div class="acct-checks wide">
        ${a.kind === "checking" || a.kind === "savings" ? `<label class="inline" title="Spreads this account's recent non-recurring spending evenly over every day of the forecast"><input type="checkbox" class="f-spend" ${a.daily_spend ? "checked" : ""}> Subtract average everyday spending</label>` : ""}
        ${owes ? `<label class="inline"><input type="checkbox" class="f-sign" ${a.owed_positive ? "checked" : ""}> Bank reports what's owed as a positive number</label>` : ""}
        <label class="inline"><input type="checkbox" class="f-hidden" ${a.hidden ? "checked" : ""}> Hide this account</label>
        <span class="type-fix">${esc(a.kind)} account · <button type="button" class="btn link type-change">change type</button>
          <select class="f-kind" hidden aria-label="Account type">${["checking", "savings", "credit", "loan", "investment"].map((k) => `<option ${k === a.kind ? "selected" : ""}>${k}</option>`).join("")}</select></span>
      </div>
    </div></details>`;
}

// Where an account's balance and transactions come from. Shown once the account is matched to a Plaid account.
function providerControl(a) {
  if (a.id.startsWith("pl:") || !a.plaid_link || !a.plaid_link.transactions) return "";
  const where = `${esc(a.plaid_link.institution || "Plaid")}${a.plaid_link.mask ? ` ••${esc(a.plaid_link.mask)}` : ""}`;
  return `<label title="Where balances and transactions come from. Switching keeps your history; transactions both have are matched up.">Transactions from
    <select class="f-provider"><option value="simplefin" ${a.provider !== "plaid" ? "selected" : ""}>SimpleFIN</option>
    <option value="plaid" ${a.provider === "plaid" ? "selected" : ""}>Plaid (${where})</option></select></label>`;
}

async function saveAccount(tr, rerender) {
  const body = { display_name: $(".f-name", tr).value, kind: $(".f-kind", tr).value, hidden: $(".f-hidden", tr).checked ? 1 : 0,
    owner: $(".f-owner", tr)?.value ?? undefined };
  if ($(".f-payfrom", tr)) body.pay_from = $(".f-payfrom", tr).value;
  if ($(".f-sign", tr)) body.owed_positive = $(".f-sign", tr).checked ? 1 : 0;
  if ($(".f-spend", tr)) body.daily_spend = $(".f-spend", tr).checked ? 1 : 0;
  try {
    await api(`/api/accounts/${encodeURIComponent(tr.dataset.id)}`, { method: "POST", body });
    if (rerender) { toast("Saved"); route(); }
  } catch (err) { if (rerender) toast(err.message, true); else throw err; }
}

// ------------------------------------------------------------------------------------------ boot
// Opening Runway (or coming back to its tab) syncs in the background when the data is more than an hour old;
// the server decides, so this is cheap to call. When the sync finishes, the page redraws with the new data.
let autoSyncWatch = null;
async function syncOnVisit() {
  try {
    const r = await api("/api/sync/auto", { method: "POST" });
    if (!r.started || autoSyncWatch) return;
    $("#sync-status").textContent = "Syncing…"; $("#sync-dot").className = "sync-dot busy";
    const before = STATE.last_sync_ok;
    autoSyncWatch = setInterval(async () => {
      await refreshState();
      if (STATE.syncing) return;
      clearInterval(autoSyncWatch); autoSyncWatch = null;
      if (STATE.last_sync_ok !== before && STATE.last_log?.ok) toast(`Synced · ${STATE.last_log.message}`);
      const f = document.activeElement;
      const busy = f && ["INPUT", "TEXTAREA", "SELECT"].includes(f.tagName) || document.querySelector(".cost-row, .tracked-editor");
      if (!busy) route();
    }, 3000);
  } catch (err) { console.error(err); }
}
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") syncOnVisit(); });

// Signing out is a POST (so no other site can sign you out with a link); then on to the provider's sign-out page.
$("#sign-out").addEventListener("click", async (e) => {
  e.preventDefault();
  try {
    const r = await api("/auth/logout", { method: "POST" });
    location.href = r.redirect || "/auth/signed-out";
  } catch (err) { toast(err.message, true); }
});

// Installable app: the service worker shows notifications and keeps the app's shell for offline starts.
if ("serviceWorker" in navigator && window.isSecureContext) {
  navigator.serviceWorker.register("/sw.js").catch((err) => console.warn("Service worker:", err));
}

(async () => {
  await refreshState();
  if (location.pathname === "/plaid/oauth") resumePlaidOAuth(); else route();
  syncOnVisit();
  setInterval(refreshState, 60_000);
})();
