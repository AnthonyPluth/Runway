// WebAuthn for the app lock (lib/lock.svelte.ts): this device's own authenticator (Face ID, Touch ID, Windows Hello,
// or the device's passcode when it offers that instead), never a security key or a phone nearby. The server issues
// every challenge and checks every answer (runway/applock.py); this only asks the device and passes the answer on.
import { errMsg } from "./act";
import type { LockChallenge, LockRegister, LockUnlock } from "./api-types";

export const b64uEncode = (b: ArrayBuffer | Uint8Array): string => {
  const bytes = b instanceof Uint8Array ? b : new Uint8Array(b);
  let s = "";
  for (const x of bytes) s += String.fromCharCode(x);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
};
export const b64uDecode = (t: string): Uint8Array<ArrayBuffer> =>
  Uint8Array.from(atob(t.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (t.length % 4)) % 4)), (c) => c.charCodeAt(0));

/** Why this device can't have an app lock, or "" when it can: it needs https, WebAuthn with the public key readable
 *  (getPublicKey: Safari 16, Chrome 85, Firefox 119 and later) and a platform authenticator that verifies the person. */
export async function unsupportedReason(): Promise<string> {
  if (!window.isSecureContext) return "https";
  const PKC = window.PublicKeyCredential;
  if (!PKC || typeof AuthenticatorAttestationResponse === "undefined" || !("getPublicKey" in AuthenticatorAttestationResponse.prototype))
    return "browser";
  try { return (await PKC.isUserVerifyingPlatformAuthenticatorAvailable()) ? "" : "device"; }
  catch { return "device"; }
}

const ALGORITHMS = [{ type: "public-key", alg: -7 }, { type: "public-key", alg: -257 }] as const;   // ES256, RS256 (Windows Hello)
const TIMEOUT_MS = 60_000;

/** A new passkey on this device for the app lock, answering the server's "register" challenge. `userId` names this
 *  device's lock, so turning it on again replaces the passkey rather than adding another. */
export async function createPasskey(ch: LockChallenge, userId: Uint8Array<ArrayBuffer>): Promise<Omit<LockRegister, "idle">> {
  const cred = await navigator.credentials.create({
    publicKey: {
      challenge: b64uDecode(ch.challenge),
      rp: { id: ch.rp_id, name: "Runway" },
      user: { id: userId, name: "Runway app lock", displayName: "Runway app lock" },
      pubKeyCredParams: [...ALGORITHMS],
      authenticatorSelection: { authenticatorAttachment: "platform", userVerification: "required", residentKey: "discouraged", requireResidentKey: false },
      attestation: "none",
      timeout: TIMEOUT_MS,
      // The passkey can give a PRF secret later (#357's encrypted cache): asked for now, so a lock turned on today
      // needn't be made again then. Nothing is evaluated yet, and a browser without PRF ignores it.
      extensions: { prf: {} },
    },
  }) as PublicKeyCredential | null;
  if (!cred) throw new Error("No passkey was made.");
  const r = cred.response as AuthenticatorAttestationResponse;
  const key = r.getPublicKey();
  if (!key) throw new Error("This device made a kind of passkey Runway can’t check. App lock can’t be used here.");
  return { credential_id: b64uEncode(cred.rawId), client_data: b64uEncode(r.clientDataJSON),
           authenticator_data: b64uEncode(r.getAuthenticatorData()), public_key: b64uEncode(key), alg: r.getPublicKeyAlgorithm() };
}

/** This device's answer to the server's "unlock" challenge, with the person verified (biometrics or the passcode), and
 *  the PRF output when one was asked for (`prfInput`, #357) and the device gave it (else null). */
export async function signChallenge(ch: LockChallenge, prfInput?: Uint8Array<ArrayBuffer>): Promise<{ answer: LockUnlock; prf: Uint8Array | null }> {
  if (!ch.credential_id) throw new Error("App lock isn’t on for this device.");
  const cred = await navigator.credentials.get({
    publicKey: {
      challenge: b64uDecode(ch.challenge),
      rpId: ch.rp_id,
      allowCredentials: [{ type: "public-key", id: b64uDecode(ch.credential_id), transports: ["internal"] }],
      userVerification: "required",
      timeout: TIMEOUT_MS,
      ...(prfInput ? { extensions: { prf: { eval: { first: prfInput } } } } : {}),
    },
  }) as PublicKeyCredential | null;
  if (!cred) throw new Error("Nothing was signed.");
  const r = cred.response as AuthenticatorAssertionResponse;
  const out = cred.getClientExtensionResults?.().prf?.results?.first;
  return {
    answer: { credential_id: b64uEncode(cred.rawId), client_data: b64uEncode(r.clientDataJSON),
              authenticator_data: b64uEncode(r.authenticatorData), signature: b64uEncode(r.signature) },
    prf: out ? new Uint8Array(out instanceof ArrayBuffer ? out : (out as ArrayBufferView).buffer.slice(0)) : null,
  };
}

/** What to tell the person when the device said no. The browser's own words name the API, not what happened. */
export function webauthnError(e: unknown): string {
  const name = (e as { name?: unknown } | null)?.name;
  if (name === "NotAllowedError" || name === "AbortError") return "Cancelled, or it took too long. Try again.";
  if (name === "SecurityError") return "This address can’t use app lock. Open Runway at its own address (RUNWAY_PUBLIC_URL).";
  if (name === "InvalidStateError") return "This device already has a passkey for that. Try again.";
  if (name === "NotSupportedError") return "This device can’t make a passkey Runway can check.";
  return errMsg(e);
}
