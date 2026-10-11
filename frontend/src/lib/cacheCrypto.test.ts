// The device cache's cryptography (lib/cacheCrypto.ts): both halves are needed, keys can't be read out, every seal
// has its own IV, and a sealed thing opens only with the same key and the same authenticated data, byte for byte.
import { describe, expect, it } from "vitest";
import { aad, cacheKey, openBytes, openText, seal, wrappingKey } from "./cacheCrypto";

const bytes = (n: number, fill: number) => new Uint8Array(n).fill(fill);
const PRF = bytes(32, 1), SHARE = bytes(32, 2);

describe("the keys", () => {
  it("are AES-GCM keys the page can use but not read", async () => {
    for (const k of [await cacheKey(PRF, SHARE), await wrappingKey(PRF)]) {
      expect(k.extractable).toBe(false);
      expect(k.algorithm).toMatchObject({ name: "AES-GCM", length: 256 });
      expect([...k.usages].sort()).toEqual(["decrypt", "encrypt"]);
      await expect(crypto.subtle.exportKey("raw", k)).rejects.toThrow();
    }
  });

  it("need both the PRF output and the share: another of either opens nothing", async () => {
    const s = await seal(await cacheKey(PRF, SHARE), aad("x"), "rows");
    expect(await openText(await cacheKey(PRF, SHARE), aad("x"), { ...s })).toBe("rows");
    await expect(openText(await cacheKey(bytes(32, 9), SHARE), aad("x"), { ...s })).rejects.toThrow();
    await expect(openText(await cacheKey(PRF, bytes(32, 9)), aad("x"), { ...s })).rejects.toThrow();
    await expect(openText(await wrappingKey(PRF), aad("x"), { ...s })).rejects.toThrow();   // the PRF output alone
  });

  it("refuse halves of the wrong length", async () => {
    await expect(cacheKey(bytes(16, 1), SHARE)).rejects.toThrow();
    await expect(cacheKey(PRF, bytes(31, 1))).rejects.toThrow();
    await expect(wrappingKey(bytes(0, 0))).rejects.toThrow();
  });

  it("leave the halves as they were given", async () => {
    const prf = bytes(32, 5), share = bytes(32, 6);
    await cacheKey(prf, share);
    expect([...prf]).toEqual([...bytes(32, 5)]);
    expect([...share]).toEqual([...bytes(32, 6)]);
  });
});

describe("sealing", () => {
  it("uses a fresh IV each time, so the same rows never look the same", async () => {
    const k = await cacheKey(PRF, SHARE);
    const a = await seal(k, aad("x"), "rows"), b = await seal(k, aad("x"), "rows");
    expect(a.iv).toHaveLength(12);
    expect([...a.iv]).not.toEqual([...b.iv]);
    expect(Buffer.from(a.ct).equals(Buffer.from(b.ct))).toBe(false);
    expect(Buffer.from(a.ct).toString("latin1")).not.toContain("rows");
  });

  it("opens only with the same authenticated data, and not after a single byte changes", async () => {
    const k = await cacheKey(PRF, SHARE);
    const s = await seal(k, aad("record", 1, "accounts", "v1"), "rows");
    expect(await openText(k, aad("record", 1, "accounts", "v1"), { ...s })).toBe("rows");
    for (const other of [aad("record", 1, "categories", "v1"), aad("record", 2, "accounts", "v1"), aad("record", 1, "accounts", "v2")])
      await expect(openText(k, other, { ...s })).rejects.toThrow();
    const ct = new Uint8Array(s.ct.slice(0));
    ct[3] ^= 1;
    await expect(openText(k, aad("record", 1, "accounts", "v1"), { iv: s.iv, ct: ct.buffer })).rejects.toThrow();
    const iv = new Uint8Array(s.iv);
    iv[0] ^= 1;
    await expect(openText(k, aad("record", 1, "accounts", "v1"), { iv, ct: s.ct })).rejects.toThrow();
  });

  it("takes bytes too, and refuses what isn't a sealed record", async () => {
    const k = await wrappingKey(PRF);
    const s = await seal(k, aad("share"), SHARE);
    expect([...await openBytes(k, aad("share"), { ...s })]).toEqual([...SHARE]);
    for (const bad of [{}, { iv: "x", ct: s.ct }, { iv: s.iv, ct: "x" }, { iv: new Uint8Array(8), ct: s.ct }])
      await expect(openBytes(k, aad("share"), bad)).rejects.toThrow();
  });
});
