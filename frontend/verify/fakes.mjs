// The stand-in services a demo server has beside it under make verify and tools/control (runway/verify.py's start_fakes,
// which runs tests/fakes/): where they are, read from the server's folder, and how the stand-in bank answers. The bank is
// a SimpleFIN bridge on a loopback port, over TLS under a made-up name, with a certificate from a throwaway authority
// that server's folder holds; only that demo server and this file ever talk to it.
import { readFileSync } from "node:fs";
import { request } from "node:https";
import { join } from "node:path";

/** runway/verify.py's FAKES_FILE, in a demo server's folder. */
export const FAKES_FILE = "fakes.json";
/** tests/fakes/simplefin_server.py's MODES (tests/test_verify_fakes.py checks they are the same). */
export const BANK_MODES = ["ok", "401", "403", "500", "slow", "partial"];

/** The stand-ins of the demo server whose folder is `dir`: { simplefin: { name, address, port, ca }, prices: { url } },
 *  or null for a server started without them. */
export function readFakes(dir) {
  try { return JSON.parse(readFileSync(join(dir, FAKES_FILE), "utf8")); } catch { return null; /* no file: a server without stand-ins */ }
}

/** Asks the stand-in bank's own `/__fake/<path>` (never a path of the bridge itself): over TLS, checked against its
 *  throwaway authority and its made-up name. Resolves with its JSON reply, rejects when it answers anything but 200. */
function ask(fakes, method, path, body, timeout) {
  const bank = fakes?.simplefin;
  if (!bank) return Promise.reject(new Error("this demo server has no stand-in bank (make verify and `tools/control start` start one beside it)"));
  return new Promise((resolve, reject) => {
    const req = request({ host: bank.address, port: bank.port, servername: bank.name, ca: readFileSync(bank.ca), method, path: `/__fake/${path}`,
      headers: { Host: bank.name, ...(body === undefined ? {} : { "Content-Type": "text/plain", "Content-Length": Buffer.byteLength(body) }) }, timeout }, (res) => {
      let text = "";
      res.setEncoding("utf8");
      res.on("data", (chunk) => { text += chunk; });
      res.on("end", () => {
        if (res.statusCode !== 200) { reject(new Error(`the stand-in bank answered ${res.statusCode}: ${text.slice(0, 200)}`)); return; }
        try { resolve(JSON.parse(text)); } catch { reject(new Error(`the stand-in bank's answer wasn't JSON: ${text.slice(0, 200)}`)); }
      });
    });
    req.on("timeout", () => req.destroy(new Error("the stand-in bank didn't answer in time")));
    req.on("error", reject);
    req.end(body);
  });
}

/** Sets how the stand-in bank answers from now on (one of BANK_MODES); resolves with { mode }. */
export function setBankMode(fakes, mode, { timeout = 10000 } = {}) {
  if (!BANK_MODES.includes(mode)) return Promise.reject(new Error(`the bank's mode is one of ${BANK_MODES.join(", ")}, not ${mode}`));
  return ask(fakes, "POST", "mode", mode, timeout);
}

/** How the stand-in bank answers now: { mode, modes }. */
export const bankMode = (fakes, { timeout = 10000 } = {}) => ask(fakes, "GET", "mode", undefined, timeout);

/** What the stand-in bank was asked since it started: [{ method, path, login, status }]. */
export const bankRequests = (fakes, { timeout = 10000 } = {}) => ask(fakes, "GET", "requests", undefined, timeout);
