// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { badgeShape, isDark } from "./logoTone";

// jsdom has no canvas, so a stand-in returns the pixels a logo would have.
function withPixels(rgba: number[], src: string) {
  const px = new Uint8ClampedArray(24 * 24 * 4);
  for (let i = 0; i < px.length; i += 4) px.set(rgba, i);
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
    drawImage: () => {}, getImageData: () => ({ data: px }),
  } as unknown as CanvasRenderingContext2D);
  const img = document.createElement("img");
  img.src = src;
  return img;
}

describe("isDark", () => {
  it("is true for a dark mark", () => expect(isDark(withPixels([20, 30, 90, 255], "/a.png"))).toBe(true));
  it("is false for a light mark", () => expect(isDark(withPixels([240, 240, 240, 255], "/b.png"))).toBe(false));
  it("ignores transparent pixels", () => expect(isDark(withPixels([0, 0, 0, 0], "/c.png"))).toBe(false));
});

// A logo w × h whose drawn pixels are `inside(x, y)` (on the canvas it's drawn to), in one color.
function shaped(src: string, w: number, h: number, inside: (x: number, y: number, cw: number, ch: number) => boolean, rgb = [20, 60, 200]) {
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
    drawImage: () => {},
    getImageData: (_x: number, _y: number, cw: number, ch: number) => {
      const px = new Uint8ClampedArray(cw * ch * 4);
      for (let y = 0; y < ch; y++) for (let x = 0; x < cw; x++) if (inside(x, y, cw, ch)) px.set([...rgb, 255], (y * cw + x) * 4);
      return { data: px };
    },
  } as unknown as CanvasRenderingContext2D);
  const img = document.createElement("img");
  img.src = src;
  Object.defineProperty(img, "naturalWidth", { value: w });
  Object.defineProperty(img, "naturalHeight", { value: h });
  return img;
}

describe("badgeShape", () => {
  it("sees a solid square tile (a bank's square logo)", () => {
    const s = badgeShape(shaped("/tile.png", 64, 64, () => true));
    expect(s.coverage).toBe(1);
    expect(s.square).toBe(true);
  });

  it("sees a wide wordmark with nothing behind it as sparse and not square", () => {
    // 4:1, the word drawn in a band across the middle with gaps between letters
    const s = badgeShape(shaped("/word.png", 128, 32, (x, y, _w, ch) => y > ch / 4 && y < (3 * ch) / 4 && x % 4 !== 0));
    expect(s.coverage).toBeLessThan(0.3);
    expect(s.square).toBe(false);
  });

  it("measures the mark's own extent, not the image's", () => {
    // a square image with a wide mark in its middle
    expect(badgeShape(shaped("/padded.png", 64, 64, (x, y) => x >= 4 && x < 28 && y >= 13 && y < 19)).square).toBe(false);
  });

  it("says when the mark is near white", () => {
    expect(badgeShape(shaped("/white.png", 64, 64, (x) => x < 10, [250, 250, 250])).light).toBe(true);
    expect(badgeShape(shaped("/blue.png", 64, 64, (x) => x < 10)).light).toBe(false);
  });

  it("keeps its answer per address", () => {
    const first = badgeShape(shaped("/same.png", 64, 64, () => true));
    expect(badgeShape(shaped("/same.png", 64, 64, () => false))).toBe(first);
  });
});
