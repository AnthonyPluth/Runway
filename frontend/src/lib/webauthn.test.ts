// @vitest-environment jsdom
// WebAuthn for the app lock (lib/webauthn.ts): which devices can have one, what's asked of the device, what's sent on,
// and what the device saying no reads as.
import { afterEach, describe, expect, it, vi } from "vitest";
import { b64uDecode, b64uEncode, createPasskey, signChallenge, unsupportedReason, webauthnError } from "./webauthn";

const bytes = (...b: number[]) => new Uint8Array(b).buffer;

afterEach(() => { vi.unstubAllGlobals(); });

function platform({ secure = true, pkc = true, getPublicKey = true, uvpa = true as boolean | Error } = {}) {
  vi.stubGlobal("isSecureContext", secure);
  vi.stubGlobal("PublicKeyCredential", pkc ? {
    isUserVerifyingPlatformAuthenticatorAvailable: () => (uvpa instanceof Error ? Promise.reject(uvpa) : Promise.resolve(uvpa)),
  } : undefined);
  vi.stubGlobal("AuthenticatorAttestationResponse", class { getPublicKey() { return null; } });
  if (!getPublicKey) vi.stubGlobal("AuthenticatorAttestationResponse", class {});
}

describe("which devices can have an app lock", () => {
  it("needs https, a browser that hands over the public key, and a platform authenticator", async () => {
    platform();
    expect(await unsupportedReason()).toBe("");
    platform({ secure: false });
    expect(await unsupportedReason()).toBe("https");
    platform({ pkc: false });
    expect(await unsupportedReason()).toBe("browser");
    platform({ getPublicKey: false });
    expect(await unsupportedReason()).toBe("browser");
    platform({ uvpa: false });
    expect(await unsupportedReason()).toBe("device");
    platform({ uvpa: new Error("nope") });
    expect(await unsupportedReason()).toBe("device");
  });
});

describe("asking the device", () => {
  it("makes a platform passkey with the person verified, and sends its public key on", async () => {
    const create = vi.fn().mockResolvedValue({
      rawId: bytes(1, 2, 3),
      response: { clientDataJSON: bytes(123, 125), getAuthenticatorData: () => bytes(9), getPublicKey: () => bytes(48, 1), getPublicKeyAlgorithm: () => -7 },
    });
    vi.stubGlobal("navigator", { credentials: { create } });
    const body = await createPasskey({ challenge: "AAEC", rp_id: "runway.example", credential_id: null }, new Uint8Array([7, 7]));
    expect(body).toEqual({ credential_id: "AQID", client_data: "e30", authenticator_data: "CQ", public_key: "MAE", alg: -7 });
    const pk = create.mock.calls[0][0].publicKey;
    expect([...pk.challenge]).toEqual([0, 1, 2]);
    expect(pk.rp).toEqual({ id: "runway.example", name: "Runway" });
    expect(pk.authenticatorSelection).toMatchObject({ authenticatorAttachment: "platform", userVerification: "required", residentKey: "discouraged" });
    expect(pk.attestation).toBe("none");
    expect(pk.extensions).toEqual({ prf: {} });   // PRF-capable from the start (#357), nothing evaluated
    expect(pk.user.name).not.toMatch(/@/);   // nothing about who you are on the device's passkey list
  });

  it("refuses a passkey whose public key the browser won't give", async () => {
    vi.stubGlobal("navigator", { credentials: { create: vi.fn().mockResolvedValue({
      rawId: bytes(1), response: { clientDataJSON: bytes(1), getAuthenticatorData: () => bytes(1), getPublicKey: () => null, getPublicKeyAlgorithm: () => -8 },
    }) } });
    await expect(createPasskey({ challenge: "AA", rp_id: "x", credential_id: null }, new Uint8Array(1))).rejects.toThrow(/can’t check/);
  });

  it("signs the unlock challenge with this lock's passkey, the person verified", async () => {
    const get = vi.fn().mockResolvedValue({
      rawId: bytes(1, 2, 3),
      response: { clientDataJSON: bytes(123, 125), authenticatorData: bytes(9), signature: bytes(255, 254) },
    });
    vi.stubGlobal("navigator", { credentials: { get } });
    expect(await signChallenge({ challenge: "AAEC", rp_id: "runway.example", credential_id: "AQID" }))
      .toEqual({ answer: { credential_id: "AQID", client_data: "e30", authenticator_data: "CQ", signature: "__4" }, prf: null });
    const pk = get.mock.calls[0][0].publicKey;
    expect(pk.userVerification).toBe("required");
    expect(pk.rpId).toBe("runway.example");
    expect([...pk.allowCredentials[0].id]).toEqual([1, 2, 3]);
    expect(pk.extensions).toBeUndefined();   // no PRF asked for until something needs it (#357)
  });

  it("asks for a PRF secret only when given an input, and hands back what the device gave", async () => {
    const get = vi.fn().mockResolvedValue({
      rawId: bytes(1), response: { clientDataJSON: bytes(1), authenticatorData: bytes(1), signature: bytes(1) },
      getClientExtensionResults: () => ({ prf: { results: { first: bytes(7, 8) } } }),
    });
    vi.stubGlobal("navigator", { credentials: { get } });
    const input = new Uint8Array(32);
    const { prf } = await signChallenge({ challenge: "AA", rp_id: "x", credential_id: "AQ" }, input);
    expect([...prf!]).toEqual([7, 8]);
    expect(get.mock.calls[0][0].publicKey.extensions).toEqual({ prf: { eval: { first: input } } });
    await expect(signChallenge({ challenge: "AA", rp_id: "x", credential_id: null })).rejects.toThrow(/isn’t on/);
  });

  it("says what the device saying no means", () => {
    const named = (name: string) => Object.assign(new Error("browser words"), { name });
    expect(webauthnError(named("NotAllowedError"))).toMatch(/Cancelled/);
    expect(webauthnError(named("SecurityError"))).toMatch(/own address/);
    expect(webauthnError(new Error("Other"))).toBe("Other");
  });

  it("round-trips base64url", () => {
    const b = new Uint8Array([0, 251, 255, 62, 63]);
    expect(b64uEncode(b)).toBe("APv_Pj8");
    expect([...b64uDecode(b64uEncode(b))]).toEqual([...b]);
  });
});
