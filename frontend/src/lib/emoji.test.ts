import { describe, expect, it } from "vitest";
import { lastEmoji } from "./categories.svelte";
import { EMOJI_WORDS, searchEmoji } from "./emoji";

describe("searchEmoji", () => {
  it("matches the start of any of an emoji's words, every word typed", () => {
    expect(searchEmoji("groc")).toContain("🛒");
    expect(searchEmoji("Coffee")).toEqual(["☕"]);
    expect(searchEmoji("gas station")).toEqual(["⛽"]);
    expect(searchEmoji("ffee")).toEqual([]);
    expect(searchEmoji("  ")).toEqual([]);
  });

  it("lists each emoji once, and only ones the server takes as an emoji", () => {
    const all = EMOJI_WORDS.map(([e]) => e);
    expect(new Set(all).size).toBe(all.length);
    for (const e of all) expect(lastEmoji(e)).toBe(e);
  });
});
