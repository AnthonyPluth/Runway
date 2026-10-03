// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

import { toast } from "svelte-sonner";
import { copyKeys, copyText } from "./copy";

const field = () => {
  const input = document.createElement("input");
  input.value = "https://runway.local/mcp";
  document.body.append(input);
  return input;
};
const clipboard = (writeText?: (s: string) => Promise<void>) =>
  Object.defineProperty(navigator, "clipboard", { value: writeText ? { writeText } : undefined, configurable: true });

beforeEach(() => { vi.mocked(toast).mockClear(); vi.mocked(toast.success).mockClear(); });
afterEach(() => { clipboard(undefined); document.body.innerHTML = ""; });

describe("copy buttons", () => {
  it("copy to the clipboard where the browser allows it", async () => {
    const writeText = vi.fn(async () => {});
    clipboard(writeText);
    await copyText("https://runway.local/mcp", field());
    expect(writeText).toHaveBeenCalledWith("https://runway.local/mcp");
    expect(toast.success).toHaveBeenCalledWith("Copied");
  });

  it("select the text and say which keys copy it on a plain-http address, where there's no clipboard", async () => {
    clipboard(undefined);
    const input = field();
    await copyText(input.value, input);
    expect(document.activeElement).toBe(input);
    expect([input.selectionStart, input.selectionEnd]).toEqual([0, input.value.length]);
    expect(toast).toHaveBeenCalledWith(expect.stringMatching(/^Selected\. (Press (⌘C|Ctrl\+C) to copy it|Tap and hold it, then Copy)\.$/));
    expect(toast.success).not.toHaveBeenCalled();
  });

  it("fall back the same way when the browser refuses", async () => {
    clipboard(async () => { throw new Error("NotAllowedError"); });
    const input = field();
    await copyText(input.value, input);
    expect(toast).toHaveBeenCalledWith(expect.stringMatching(/^Selected\./));
  });

  it("name the keys for the device", () => {
    expect(copyKeys("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0)")).toBe("Press ⌘C to copy it");
    expect(copyKeys("Mozilla/5.0 (Windows NT 10.0; Win64; x64)")).toBe("Press Ctrl+C to copy it");
    expect(copyKeys("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)")).toBe("Tap and hold it, then Copy");
  });
});
