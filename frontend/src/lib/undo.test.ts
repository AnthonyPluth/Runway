import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { error: vi.fn() }) }));

import { toast } from "svelte-sonner";
import { UNDO_MS, undoable } from "./undo";

type Opts = { description?: string; duration: number; action: { label: string; onClick: () => Promise<void> } };
const shown = () => vi.mocked(toast).mock.calls[0] as [string, Opts];

beforeEach(() => { vi.mocked(toast).mockClear(); vi.mocked(toast.error).mockClear(); });

describe("undoable", () => {
  it("shows the message with an Undo that stays longer than a plain toast", () => {
    undoable("Moved to Dining", async () => {}, { description: "Blue Bottle" });
    const [msg, opts] = shown();
    expect(msg).toBe("Moved to Dining");
    expect(opts.description).toBe("Blue Bottle");
    expect(opts.action.label).toBe("Undo");
    expect(opts.duration).toBe(UNDO_MS);
    expect(UNDO_MS).toBeGreaterThan(4000);
  });

  it("takes a duration of its own", () => {
    undoable("Done", async () => {}, { duration: 20_000 });
    expect(shown()[1].duration).toBe(20_000);
  });

  it("does nothing until Undo is clicked, then runs the undo and says so", async () => {
    const undo = vi.fn(async () => {});
    undoable("Done", undo);
    expect(undo).not.toHaveBeenCalled();
    await shown()[1].action.onClick();
    expect(undo).toHaveBeenCalledOnce();
    expect(toast).toHaveBeenLastCalledWith("Undone", undefined);
  });

  it("shows what the undo reports as the description of 'Undone'", async () => {
    undoable("Done", async () => "Venture X is open again");
    await shown()[1].action.onClick();
    expect(toast).toHaveBeenLastCalledWith("Undone", { description: "Venture X is open again" });
  });

  it("shows the error, not 'Undone', when the undo fails", async () => {
    undoable("Done", async () => { throw new Error("Already gone"); });
    await shown()[1].action.onClick();
    expect(toast.error).toHaveBeenCalledWith("Already gone");
    expect(toast).toHaveBeenCalledTimes(1);   // only the original toast
  });

  it("has words for a failure that has none", async () => {
    undoable("Done", async () => { throw new Error(""); });
    await shown()[1].action.onClick();
    expect(toast.error).toHaveBeenCalledWith("Couldn’t undo that");
  });
});
