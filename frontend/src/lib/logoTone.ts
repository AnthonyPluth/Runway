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
