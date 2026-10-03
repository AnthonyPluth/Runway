// Copy buttons. The browser only lets a page write to the clipboard over https (or on localhost), so on a plain-http
// address on the home network the text is selected instead, with a toast saying which keys copy it.
import { toast } from "svelte-sonner";

/** The keys (or tap) that copy what's selected, on this device. */
export function copyKeys(ua = typeof navigator === "undefined" ? "" : navigator.userAgent): string {
  if (/iPhone|iPad|Android/.test(ua)) return "Tap and hold it, then Copy";
  return /Mac/.test(ua) ? "Press ⌘C to copy it" : "Press Ctrl+C to copy it";
}

/** Copy `text`; where that isn't allowed, select it in `field` (the box showing it) and say how to copy it. */
export async function copyText(text: string, field?: HTMLInputElement | null): Promise<void> {
  try {
    if (!navigator.clipboard?.writeText) throw new Error("no clipboard here");
    await navigator.clipboard.writeText(text);
    toast.success("Copied");
  } catch {
    field?.focus();
    field?.select();
    toast(field ? `Selected. ${copyKeys()}.` : copyKeys());
  }
}
