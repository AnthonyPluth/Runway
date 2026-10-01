// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { b64uToBytes, currentSubscription, deviceName, isIOS, pushSupported } from "./push";

const setUA = (ua: string, platform = "", touch = 0) => {
  // (jsdom lacks maxTouchPoints, so define all three rather than spy on getters)
  for (const [k, value] of Object.entries({ userAgent: ua, platform, maxTouchPoints: touch })) Object.defineProperty(navigator, k, { value, configurable: true });
};
const standalone = (on: boolean) => vi.stubGlobal("matchMedia", () => ({ matches: on }));
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("b64uToBytes", () => {
  it("decodes URL-safe base64 without padding (a VAPID key)", () => {
    // "hello?>" is "aGVsbG8/Pg" in url-safe base64 with the padding dropped
    expect(Array.from(b64uToBytes("aGVsbG8_Pg"))).toEqual([..."hello?>"].map((c) => c.charCodeAt(0)));
  });
});

describe("deviceName", () => {
  it("names the device and the browser", () => {
    standalone(false);
    setUA("Mozilla/5.0 (Macintosh; Intel Mac OS X) Chrome/120 Safari/537");
    expect(deviceName()).toBe("Mac · Chrome");
    setUA("Mozilla/5.0 (Windows NT 10.0) Edg/120 Chrome/120 Safari/537");
    expect(deviceName()).toBe("Windows · Edge");
    setUA("Mozilla/5.0 (X11; Linux) Firefox/120.0");
    expect(deviceName()).toBe("Computer · Firefox");
    setUA("Mozilla/5.0 (iPhone; CPU iPhone OS 17) Safari/604");
    expect(deviceName()).toBe("iPhone · Safari");
  });

  it("says 'app' when it's installed to the home screen", () => {
    standalone(true);
    setUA("Mozilla/5.0 (iPhone; CPU iPhone OS 17) Safari/604");
    expect(deviceName()).toBe("iPhone · app");
  });

  it("recognizes an iPad that reports itself as a Mac", () => {
    standalone(false);
    setUA("Mozilla/5.0 (Macintosh) Safari/605", "MacIntel", 5);
    expect(isIOS()).toBe(true);
    expect(deviceName().startsWith("iPad")).toBe(true);
  });
});

describe("push support", () => {
  it("isn't supported where there's no service worker, push manager or notifications", async () => {
    expect(pushSupported()).toBe(false);   // jsdom has none of them
    expect(await currentSubscription()).toEqual({ reg: null, sub: null });
  });

  it("returns this device's registration and subscription", async () => {
    const sub = { endpoint: "https://push/1" };
    const reg = { pushManager: { getSubscription: vi.fn().mockResolvedValue(sub) } };
    vi.stubGlobal("PushManager", class {});
    vi.stubGlobal("Notification", class {});
    vi.stubGlobal("isSecureContext", true);
    Object.defineProperty(navigator, "serviceWorker", { value: { register: vi.fn().mockResolvedValue(reg) }, configurable: true });
    expect(await currentSubscription()).toEqual({ reg, sub });
    expect(navigator.serviceWorker.register).toHaveBeenCalledWith("/sw.js");
    vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.mocked(navigator.serviceWorker.register).mockRejectedValue(new Error("blocked"));
    expect(await currentSubscription()).toEqual({ reg: null, sub: null });
    delete (navigator as unknown as Record<string, unknown>).serviceWorker;
  });

  it("uses the app's registration, once its worker is ready", async () => {
    const sub = { endpoint: "https://push/1" };
    const starting = { active: null, pushManager: { getSubscription: vi.fn().mockResolvedValue(null) } };
    const ready = { active: {}, pushManager: { getSubscription: vi.fn().mockResolvedValue(sub) } };
    vi.stubGlobal("PushManager", class {});
    vi.stubGlobal("Notification", class {});
    vi.stubGlobal("isSecureContext", true);
    const register = vi.fn();
    Object.defineProperty(navigator, "serviceWorker", { value: { getRegistration: vi.fn().mockResolvedValue(starting), register, ready: Promise.resolve(ready) }, configurable: true });
    expect(await currentSubscription()).toEqual({ reg: ready, sub });
    expect(register).not.toHaveBeenCalled();
    delete (navigator as unknown as Record<string, unknown>).serviceWorker;
  });
});
