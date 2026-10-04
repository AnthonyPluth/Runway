// Small helpers every part of the background worker uses. Loaded by background.js (importScripts in Chrome, the manifest's
// scripts list in Firefox), like the other files here: classic scripts that share one global scope.
/* exported MAX_PAGES, PAUSE_MS, sleep, withTimeout, inParallel */

const MAX_PAGES = 60;
const PAUSE_MS = 400;        // between order pages, to go at a person's pace rather than hammer the store



const sleep = (ms) => new Promise((r) => setTimeout(r, ms));



// The promise, or an error once `ms` have passed without it settling (so a stalled page can't hold an import forever).
function withTimeout(promise, ms, message, ErrorType = Error) {
  let timer;
  const late = new Promise((_, reject) => { timer = setTimeout(() => reject(new ErrorType(message)), ms); });
  return Promise.race([promise, late]).finally(() => clearTimeout(timer));
}



// Runs fn over items, `width` at a time; the first failure stops it.
async function inParallel(items, width, fn) {
  let next = 0, failed = false;
  await Promise.all(Array.from({ length: Math.min(width, items.length) }, async () => {
    try {
      while (next < items.length && !failed) await fn(items[next++]);
    } catch (e) {
      failed = true;
      throw e;
    }
  }));
}
