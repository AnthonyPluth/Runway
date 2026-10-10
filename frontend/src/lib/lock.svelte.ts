// The app lock on this device: Face ID, Touch ID or the device's passcode before Runway shows anything (Settings → Data).
//
// The server is what holds it (runway/applock.py): while this sign-in's lock is locked it refuses every call for data
// (423), so the lock doesn't depend on this code. What this does is make the lock the first and only thing drawn:
// whether the lock is on is remembered on the device (localStorage), so a launch draws the lock screen and asks the
// server for nothing but the lock until it's unlocked (main.ts waits with Overview's early request and the state). It
// locks again when the app comes back after `idle` seconds away (and tells the server), covers the page while the app
// is in the background (the app switcher's preview), and draws the lock screen whenever the server says it's locked.
//
//   off ──turn on──▶ unlocked ──away ≥ idle, Lock now, or a 423──▶ locked ──unlock (server-checked)──▶ unlocked
//    ▲                                                                │
//    └──────────── turn off (unlocked), sign out, or the server has no lock for this sign-in ─────────┘
import { errMsg } from "./act";
import { api, forgetReplies, newPage } from "./api";
import { apiCall } from "./contract";
import { clearCache } from "./swr";
import { signChallenge, webauthnError } from "./webauthn";
import type { LockStatus } from "./api-types";
import { toast } from "svelte-sonner";

export const IDLE_CHOICES = [
  { seconds: 0, label: "Immediately" }, { seconds: 60, label: "After 1 minute" },
  { seconds: 300, label: "After 5 minutes" }, { seconds: 900, label: "After 15 minutes" },
] as const;
export const DEFAULT_IDLE = 60;

const KEY = "runway.lock";
const USER_KEY = "runway.lock.user";
type Saved = { on: boolean; idle: number };
type Phase = "off" | "locked" | "unlocked";

/** What this device remembers: whether the lock is on here, and how long away locks it. Unreadable means off. */
export function readSaved(): Saved {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? "null") as Partial<Saved> | null;
    const idle = IDLE_CHOICES.some((c) => c.seconds === v?.idle) ? v!.idle! : DEFAULT_IDLE;
    return { on: v?.on === true, idle };
  } catch { return { on: false, idle: DEFAULT_IDLE }; }
}
function save(s: Saved | null): void {
  try { if (s) localStorage.setItem(KEY, JSON.stringify(s)); else localStorage.removeItem(KEY); }
  catch { /* not remembered: the server still locks, and the lock screen then shows on its first 423 */ }
}

const saved = readSaved();
export const lock = $state({
  phase: (saved.on ? "locked" : "off") as Phase,
  idle: saved.idle,
  /** The app is in the background with the lock on: the page is covered, so the app switcher's preview shows nothing. */
  covered: false,
  busy: false,
  error: "",
  /** Locked since the app was opened: the lock screen locks the server too (lockScreenShown). */
  launch: saved.on,
  /** The lock screen offers the device's prompt on its own (not after Lock now: you just locked it). */
  offer: true,
});

export const isLocked = (): boolean => lock.phase === "locked";

// What waits for the first unlock (main.ts: Runway's state, Overview's early request): straight away when there's no lock.
const onFirstUnlock: (() => void)[] = [];
let started = !saved.on;   // without the lock, the app loads from the start
/** Run `fn` once the app may load data: now if it isn't locked, else after the first unlock. */
export function whenUnlocked(fn: () => void): void {
  if (started || !isLocked()) { started = true; fn(); } else onFirstUnlock.push(fn);
}
const onEveryUnlock: (() => void)[] = [];
/** Run `fn` after each unlock but the first (lib/app.svelte.ts catches up on what changed while it was locked). */
export function afterUnlock(fn: () => void): void { onEveryUnlock.push(fn); }
function opened(phase: Phase = "unlocked"): void {
  lock.phase = phase;
  lock.error = "";
  if (started) { onEveryUnlock.forEach((fn) => fn()); return; }
  started = true;
  onFirstUnlock.splice(0).forEach((fn) => fn());
}

/** The server's word on this device's lock, kept: on (with how long away locks it), or off. */
function adopt(s: LockStatus): void {
  if (!s.on) { forget(); return; }
  lock.idle = s.idle;
  save({ on: true, idle: s.idle });
}

/** Forget the lock on this device (signing out, turning it off, or the server has none), and what an unlock held. */
export function forget(): void {
  wipeSecret();
  clearCache(); forgetReplies();   // both kept copies of replies (lib/swr.ts, lib/api.ts's ETag ones)
  save(null);
}

// ------------------------------------------------------------------------------------------ the unlocked secret
/** What the last unlock proved, for what may later need it: #357's encrypted cache will make its key from the passkey's
 *  PRF output (`prf`, when the unlock asked for it and the device gave it) and a share the server holds for this device
 *  (`deviceId`). Held only while unlocked: locking, the re-lock timers, signing out and a 401 zero it and drop it. */
export interface Unlocked { deviceId: string | null; credentialId: string | null; at: number; prf: Uint8Array | null }
let secret: Unlocked | null = null;
/** The unlocked secret, or null whenever Runway isn't unlocked on this device. */
export const unlockedSecret = (): Unlocked | null => (lock.phase === "unlocked" ? secret : null);
let maxTimer: ReturnType<typeof setTimeout> | undefined;
/** An unlock lasts MAX_UNLOCKED_MS however much it's used, as on the server (runway/applock.py MAX_UNLOCKED). */
export const MAX_UNLOCKED_MS = 12 * 3600 * 1000;
function hold(s: LockStatus, prf: Uint8Array | null = null): void {
  wipeSecret();
  secret = { deviceId: s.device_id, credentialId: s.credential_id, at: Date.now(), prf };
  maxTimer = setTimeout(() => lockNow(), MAX_UNLOCKED_MS);
}
/** Zero and drop what the last unlock held. */
export function wipeSecret(): void {
  secret?.prf?.fill(0);
  secret = null;
  clearTimeout(maxTimer);
}

/** Lock now: draw the lock screen (the page goes, and with it what it showed) and tell the server, so the data stays
 *  locked there too. `tell` is false when it was the server that said so; `offer` false when you locked it yourself
 *  (Lock now), so Face ID isn't asked for straight back. */
export function lockNow(tell = true, offer = true): void {
  if (lock.phase === "off") return;
  lock.offer = offer;
  lock.phase = "locked";
  wipeSecret();
  clearCache(); forgetReplies();   // what the lists remembered for instant paint (lib/swr.ts) and the replies kept
                                   // for 304s (lib/api.ts) go with the page
  clearTimeout(awayTimer);
  lock.covered = false;
  lock.error = "";
  newPage();          // the page's reads in flight are dropped
  toast.dismiss();    // a toast can say what a page did ("Synced · 3 new transactions")
  if (tell) {
    api("/api/lock/engage", { method: "POST", keepalive: true, background: true })
      .catch(() => { /* the server locks on its own once the app stops checking in (applock.GRACE) */ });
  }
}

/** The lock screen is up. Opening the app (launch) locks the server too, and learns whether this sign-in still has a
 *  lock: signing in again (after the last session ran out) starts without one. Returns whether it's still locked. */
export async function lockScreenShown(): Promise<boolean> {
  if (!lock.launch) return isLocked();
  lock.launch = false;
  try {
    const s = await apiCall<"POST /api/lock/engage">("/api/lock/engage", { method: "POST" });
    adopt(s);
    if (!s.on) {
      opened("off");
      toast("App lock is off on this device, because you signed in again. Turn it back on in Settings → Data.");
      return false;
    }
  } catch (err) {
    // No sign-in any more (the server has no lock without it): nothing to lock. Anything else (offline): stay locked.
    if ((err as { status?: number }).status === 400) { forget(); opened("off"); return false; }
    lock.error = errMsg(err);
    lock.launch = true;   // try again with the unlock
  }
  return true;
}

/** Ask the device (Face ID, Touch ID or the passcode) and have the server check it. `quiet`: an attempt the app made on
 *  its own, whose being turned down (no tap first, on some browsers) isn't worth an error line. */
export async function unlock(quiet = false): Promise<boolean> {
  if (lock.busy || !isLocked()) return false;
  lock.busy = true;
  lock.error = "";
  try {
    if (lock.launch && !(await lockScreenShown())) return true;
    const ch = await apiCall<"POST /api/lock/challenge">("/api/lock/challenge", { method: "POST", body: { purpose: "unlock" } });
    let signed;
    try { signed = await signChallenge(ch); }
    catch (err) {
      if (!quiet) lock.error = webauthnError(err);
      return false;
    }
    const s = await apiCall<"POST /api/lock/unlock">("/api/lock/unlock", { method: "POST", body: signed.answer });
    adopt(s);
    if (s.locked) { signed.prf?.fill(0); lock.error = "Runway is still locked. Try again."; return false; }
    hold(s, signed.prf);
    opened();
    return true;
  } catch (err) {
    if ((err as { status?: number }).status === 400 && /isn’t on/.test(errMsg(err))) {   // turned off meanwhile
      forget(); opened("off"); return true;
    }
    lock.error = errMsg(err);
    return false;
  } finally {
    lock.busy = false;
  }
}

/** The lock was turned on here (Settings → Data): it's unlocked, since you just proved it was you. */
export function turnedOn(s: LockStatus): void {
  adopt(s);
  if (lock.phase !== "unlocked" || secret?.deviceId !== s.device_id) hold(s);
  lock.phase = "unlocked";
  started = true;
}

/** The lock was turned off here. */
export function turnedOff(): void {
  forget();
  lock.phase = "off";
  lock.covered = false;
}

/** The id this device's lock passkeys are made under, so turning the lock on again replaces the passkey. */
export function deviceUserId(): Uint8Array<ArrayBuffer> {
  try {
    const v = localStorage.getItem(USER_KEY);
    if (v && /^[0-9a-f]{32}$/.test(v)) return Uint8Array.from(v.match(/../g)!.map((h) => parseInt(h, 16)));
  } catch { /* a new one, below */ }
  const id = crypto.getRandomValues(new Uint8Array(16));
  try { localStorage.setItem(USER_KEY, [...id].map((b) => b.toString(16).padStart(2, "0")).join("")); }
  catch { /* a new id next time: another passkey, which only adds one to the device's list */ }
  return id;
}

// ------------------------------------------------------------------------------------------ away and back
let hiddenAt: number | null = null;
let awayTimer: ReturnType<typeof setTimeout> | undefined;
/** The app went to the background (`now`: Date.now(), the wall clock, which keeps going while a phone sleeps). It
 *  locks when `idle` runs out there (a timer, which a phone may hold back while the app sleeps: then on coming back). */
export function wentAway(now = Date.now()): void {
  hiddenAt = now;
  if (lock.phase !== "unlocked") return;
  if (lock.idle === 0) { lockNow(); return; }
  lock.covered = true;
  clearTimeout(awayTimer);
  awayTimer = setTimeout(() => { if (hiddenAt !== null) lockNow(); }, lock.idle * 1000);
}
/** The app is back: locked again if it was away `idle` seconds or more, else uncovered. */
export function cameBack(now = Date.now()): void {
  clearTimeout(awayTimer);
  const away = hiddenAt === null ? 0 : now - hiddenAt;
  hiddenAt = null;
  if (lock.phase === "unlocked" && away >= lock.idle * 1000) lockNow();
  lock.covered = false;
}

if (typeof document !== "undefined") {
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") wentAway(); else cameBack(); });
  // Back from the back/forward cache (a page kept whole while away): as if it came back to the screen.
  window.addEventListener("pageshow", (e) => { if (e.persisted) cameBack(); });
  // The server said this sign-in is locked (its lock ran out, or another tab locked it): it's on, whatever the device
  // remembered (its storage may have been cleared).
  // Signed out (a 401): the sign-in, and the lock with it, is gone on the server; so is what the device held for it.
  window.addEventListener("runway:signed-out", () => forget());
  window.addEventListener("runway:locked", () => {
    if (lock.phase === "locked") return;
    save({ on: true, idle: lock.idle });
    lock.phase = "unlocked";
    lockNow(false);
  });
}
