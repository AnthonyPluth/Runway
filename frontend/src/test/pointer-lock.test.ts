// @vitest-environment jsdom
// The shared setup makes pointer actions wait out the page lock Bits UI leaves on <body> for a moment after a sheet closes.
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => { document.body.innerHTML = ""; });

const button = () => {
  const onclick = vi.fn();
  const b = document.createElement("button");
  b.textContent = "Retry"; b.onclick = onclick;
  document.body.append(b);
  return { b, onclick };
};

describe("pointer actions right after a sheet closes", () => {
  it("wait for the body lock to lift, directly and from setup()", async () => {
    for (const user of [userEvent, userEvent.setup()]) {
      const { b, onclick } = button();
      document.body.style.pointerEvents = "none";
      setTimeout(() => { document.body.style.pointerEvents = ""; }, 60);
      await user.click(b);
      expect(onclick).toHaveBeenCalledTimes(1);
      b.remove();
    }
  });

  it("still refuse while a dialog is open (the lock is then meant)", async () => {
    const { b, onclick } = button();
    document.body.style.pointerEvents = "none";
    const dialog = document.createElement("div");
    dialog.setAttribute("role", "dialog");
    document.body.append(dialog);
    await expect(userEvent.click(b)).rejects.toThrow(/pointer-events: none/);
    expect(onclick).not.toHaveBeenCalled();
  });
});
