// The device cache's cryptography (lib/deviceCache.ts), WebCrypto only. Two keys come from the passkey's PRF output (a
// secret only the passkey, with Face ID, Touch ID or the passcode, can give for this input):
//   - the wrapping key, from the PRF output alone: it seals the device's own copy of the server's key share, so the
//     copy kept on the device is useless without the passkey;
//   - the cache key, from the PRF output and the server's share together (HKDF over prf ‖ share, 32 bytes each): it
//     seals the records, so neither the passkey nor the share alone opens them, and the server forgetting the share
//     (the device's lock ending, runway/applock.py) leaves them unreadable.
// Both are AES-GCM-256, made non-extractable (the page can use them, not read them), and held only in memory. Every
// seal has a fresh random 96-bit IV and authenticated data saying what it is (which record, which schema, which data
// version; for the share, which device and until when), so a record moved to another name, or its tag or expiry
// changed, doesn't open.
const enc = new TextEncoder();
const dec = new TextDecoder();

/** What the unlock asks the passkey's PRF to evaluate (the same every time, so the same passkey gives the same secret). */
export const PRF_INPUT: Uint8Array<ArrayBuffer> = enc.encode("Runway on-device cache, v1");
/** The PRF output and the share are each this long; anything else isn't used. */
export const SECRET_BYTES = 32;
const SALT = enc.encode("runway.cache.v1");

async function derive(ikm: Uint8Array, info: string): Promise<CryptoKey> {
  const base = await crypto.subtle.importKey("raw", ikm as Uint8Array<ArrayBuffer>, "HKDF", false, ["deriveKey"]);
  return crypto.subtle.deriveKey({ name: "HKDF", hash: "SHA-256", salt: SALT, info: enc.encode(info) }, base,
    { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
}

function check(b: Uint8Array, what: string): void {
  if (!(b instanceof Uint8Array) || b.length !== SECRET_BYTES) throw new Error(`${what} isn’t ${SECRET_BYTES} bytes`);
}

/** The key that seals the device's copy of the share: from the PRF output alone. */
export async function wrappingKey(prf: Uint8Array): Promise<CryptoKey> {
  check(prf, "The PRF output");
  return derive(prf, "runway.cache.share-wrap");
}

/** The key that seals the records: from the PRF output and the server's share. The joined copy is zeroed once used. */
export async function cacheKey(prf: Uint8Array, share: Uint8Array): Promise<CryptoKey> {
  check(prf, "The PRF output");
  check(share, "The share");
  const ikm = new Uint8Array(SECRET_BYTES * 2);
  ikm.set(prf, 0);
  ikm.set(share, SECRET_BYTES);
  try { return await derive(ikm, "runway.cache.records"); }
  finally { ikm.fill(0); }
}

/** The authenticated data for a sealed thing: its parts, unambiguously joined. */
export const aad = (...parts: (string | number)[]): Uint8Array<ArrayBuffer> => enc.encode(JSON.stringify(parts));

export interface Sealed { iv: Uint8Array<ArrayBuffer>; ct: ArrayBuffer }

/** `plain` (text or bytes) sealed under `key` with a fresh random IV, bound to `ad`. */
export async function seal(key: CryptoKey, ad: Uint8Array<ArrayBuffer>, plain: string | Uint8Array<ArrayBuffer>): Promise<Sealed> {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const data = typeof plain === "string" ? enc.encode(plain) : plain;
  return { iv, ct: await crypto.subtle.encrypt({ name: "AES-GCM", iv, additionalData: ad }, key, data) };
}

/** What `seal` sealed, as bytes; throws when the key, the IV, the authenticated data or a single byte differ. */
export async function openBytes(key: CryptoKey, ad: Uint8Array<ArrayBuffer>, s: Record<string, unknown>): Promise<Uint8Array<ArrayBuffer>> {
  // (checked by tag rather than instanceof: what IndexedDB hands back may come from another realm)
  const tag = (v: unknown) => Object.prototype.toString.call(v);
  const iv = tag(s.iv) === "[object Uint8Array]" ? s.iv as Uint8Array<ArrayBuffer> : null;
  const ct = tag(s.ct) === "[object ArrayBuffer]" ? s.ct as ArrayBuffer : null;
  if (!iv || iv.length !== 12 || !ct) throw new Error("Not a sealed record");
  return new Uint8Array(await crypto.subtle.decrypt({ name: "AES-GCM", iv, additionalData: ad }, key, ct));
}

/** What `seal` sealed, as text. */
export async function openText(key: CryptoKey, ad: Uint8Array<ArrayBuffer>, s: Record<string, unknown>): Promise<string> {
  return dec.decode(await openBytes(key, ad, s));
}
