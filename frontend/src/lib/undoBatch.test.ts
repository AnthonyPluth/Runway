// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { error: vi.fn() }) }));

import { toast } from "svelte-sonner";
import { UNDO_MS } from "./undo";
import { endBatch, undoBatched } from "./undoBatch";

type Btn = { label: string; onClick: () => Promise<void> };
type Opts = { id: string; description?: string; duration: number; action: Btn; cancel?: Btn; onDismiss: () => void; onAutoClose: () => void };
const last = () => vi.mocked(toast).mock.calls.at(-1) as [string, Opts];

beforeEach(() => { endBatch(); vi.mocked(toast).mockClear(); vi.mocked(toast.error).mockClear(); });

describe("undoBatched", () => {
  it("shows the first change on its own, with Undo", () => {
    undoBatched("Uncategorized → Coffee", async () => {}, { description: "Blue Bottle" });
    const [msg, opts] = last();
    expect(msg).toBe("Uncategorized → Coffee");
    expect(opts.description).toBe("Blue Bottle");
    expect(opts.action.label).toBe("Undo");
    expect(opts.duration).toBe(UNDO_MS);
  });

  it("folds the next changes into the same toast, and Undo puts them all back, newest first", async () => {
    const order: string[] = [];
    undoBatched("A → Coffee", async () => { order.push("a"); });
    const id = last()[1].id;
    undoBatched("B → Groceries", async () => { order.push("b"); });
    undoBatched("C → Dining", async () => { order.push("c"); });
    const [msg, opts] = last();
    expect(opts.id).toBe(id);
    expect(msg).toBe("3 changed");
    expect(opts.description).toBe("C → Dining");
    await opts.action.onClick();
    expect(order).toEqual(["c", "b", "a"]);
    expect(toast).toHaveBeenLastCalledWith("Undone", { description: "3 changes" });
  });

  it("starts a new toast once the last one has gone", () => {
    undoBatched("A", async () => {});
    const first = last()[1];
    first.onAutoClose();
    undoBatched("B", async () => {});
    expect(last()[0]).toBe("B");
    expect(last()[1].id).not.toBe(first.id);
  });

  it("starts afresh after an Undo", async () => {
    undoBatched("A", async () => {});
    await last()[1].action.onClick();
    undoBatched("B", async () => {});
    expect(last()[0]).toBe("B");
  });

  it("makes the newest change's extra action the main button, with Undo beside it", async () => {
    const run = vi.fn(async () => {}), undo = vi.fn(async () => {});
    undoBatched("Groceries", undo, { also: { label: "Always for Whole Foods", run } });
    const opts = last()[1];
    expect(opts.action.label).toBe("Always for Whole Foods");
    expect(opts.cancel!.label).toBe("Undo");
    await opts.action.onClick();
    expect(run).toHaveBeenCalledOnce();
    expect(undo).not.toHaveBeenCalled();
  });

  it("shows an error from Undo instead of 'Undone'", async () => {
    undoBatched("A", async () => { throw new Error("Already gone"); });
    await last()[1].action.onClick();
    expect(toast.error).toHaveBeenCalledWith("Already gone");
  });

  it("stays up while a key is on it", () => {
    undoBatched("A", async () => {});
    const el = document.createElement("li");
    el.setAttribute("data-sonner-toast", "");
    const button = document.createElement("button");
    el.append(button); document.body.append(el);
    button.focus();
    expect(last()[1].duration).toBe(Number.POSITIVE_INFINITY);
    button.blur();
    expect(last()[1].duration).toBe(UNDO_MS);
    el.remove();
  });
});
