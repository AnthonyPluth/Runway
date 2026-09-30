// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { isDark } from "./logoTone";

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
