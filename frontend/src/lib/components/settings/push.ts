// Push notifications on this device. They go through the service worker at /sw.js (registered when the app opens, see
// main.ts); registering it again from here is the same registration.

export const b64uToBytes = (t: string) =>
  Uint8Array.from(atob(t.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (t.length % 4)) % 4)), (c) => c.charCodeAt(0));
export const isIOS = () => /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
export const isInstalled = () =>
  window.matchMedia("(display-mode: standalone)").matches || (navigator as Navigator & { standalone?: boolean }).standalone === true;

/** "iPhone · app", "Mac · Chrome": how this device is listed under Devices. */
export function deviceName(): string {
  const ua = navigator.userAgent;
  const dev = /iPhone/.test(ua) ? "iPhone" : /iPad/.test(ua) || isIOS() ? "iPad" : /Android/.test(ua) ? "Android" : /Mac/.test(ua) ? "Mac" : /Windows/.test(ua) ? "Windows" : "Computer";
  const br = isInstalled() ? "app" : /Edg\//.test(ua) ? "Edge" : /Firefox\//.test(ua) ? "Firefox" : /Chrome\//.test(ua) ? "Chrome" : /Safari\//.test(ua) ? "Safari" : "browser";
  return `${dev} · ${br}`;
}

export const pushSupported = () => "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;

/** This device's service worker registration and push subscription, if any. A worker that's still starting (the app
 *  was just opened) may not know its subscription yet, so this waits for it to be ready (for a few seconds at most). */
export async function currentSubscription(wait = 3000): Promise<{ reg: ServiceWorkerRegistration | null; sub: PushSubscription | null }> {
  if (!pushSupported() || !window.isSecureContext) return { reg: null, sub: null };
  try {
    const sw = navigator.serviceWorker;
    let reg = (await sw.getRegistration?.()) || (await sw.register("/sw.js"));
    if (!reg.active && sw.ready)
      reg = await Promise.race([sw.ready, new Promise<ServiceWorkerRegistration>((ok) => setTimeout(() => ok(reg), wait))]);
    return { reg, sub: await reg.pushManager.getSubscription() };
  } catch (e) { console.warn(e); return { reg: null, sub: null }; }
}
