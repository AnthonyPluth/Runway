// Whether a logo is too dark to read on the dark page: the average brightness of what it actually draws (its opaque
// pixels). Logos come from Runway's own address, so the browser lets the page read them. Answers are kept per address.
const known = new Map<string, boolean>();

export function isDark(img: HTMLImageElement): boolean {
  const hit = known.get(img.currentSrc || img.src);
  if (hit !== undefined) return hit;
  let dark = false;
  try {
    const size = 24, c = document.createElement("canvas");
    c.width = c.height = size;
    const ctx = c.getContext("2d", { willReadFrequently: true });
    if (ctx) {
      ctx.drawImage(img, 0, 0, size, size);
      const px = ctx.getImageData(0, 0, size, size).data;
      let sum = 0, n = 0;
      for (let i = 0; i < px.length; i += 4) {
        const a = px[i + 3] / 255;
        if (a < 0.5) continue;
        sum += 0.2126 * px[i] + 0.7152 * px[i + 1] + 0.0722 * px[i + 2];
        n++;
      }
      dark = n > 0 && sum / n < 80;
    }
  } catch { /* unreadable image: leave it as it is */ }
  known.set(img.currentSrc || img.src, dark);
  return dark;
}

/** What a logo draws, for a small badge: how much of a square it covers (its opaque pixels' share, drawn to fit), whether
 *  what it draws is about square (a tile, like Chase's, rather than a wordmark, like Citi's), and whether it's near
 *  white (a white wordmark wants a dark tile behind it). */
export interface BadgeShape { coverage: number; square: boolean; light: boolean }
const shapes = new Map<string, BadgeShape>();
const SIDE = 32;

export function badgeShape(img: HTMLImageElement): BadgeShape {
  const key = img.currentSrc || img.src;
  const hit = shapes.get(key);
  if (hit) return hit;
  let out: BadgeShape = { coverage: 1, square: true, light: false };   // unreadable: drawn as it is
  try {
    // Drawn at its own proportions, as object-contain draws it in the badge.
    const ratio = img.naturalWidth && img.naturalHeight ? img.naturalHeight / img.naturalWidth : 1;
    const w = ratio <= 1 ? SIDE : Math.max(1, Math.round(SIDE / ratio)), h = ratio <= 1 ? Math.max(1, Math.round(SIDE * ratio)) : SIDE;
    const c = document.createElement("canvas");
    c.width = w; c.height = h;
    const ctx = c.getContext("2d", { willReadFrequently: true });
    if (ctx) {
      ctx.drawImage(img, 0, 0, w, h);
      const px = ctx.getImageData(0, 0, w, h).data;
      let n = 0, sum = 0, x0 = w, y0 = h, x1 = -1, y1 = -1;
      for (let i = 0; i < px.length; i += 4) {
        if (px[i + 3] < 128) continue;
        const x = (i / 4) % w, y = Math.floor(i / 4 / w);
        n++; sum += 0.2126 * px[i] + 0.7152 * px[i + 1] + 0.0722 * px[i + 2];
        x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y);
      }
      const bw = x1 - x0 + 1, bh = y1 - y0 + 1;
      out = { coverage: n / (SIDE * SIDE), square: n > 0 && Math.max(bw, bh) / Math.min(bw, bh) <= 1.3, light: n > 0 && sum / n > 200 };
    }
  } catch { /* unreadable image: leave it as it is */ }
  shapes.set(key, out);
  return out;
}
