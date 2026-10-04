// A change that has already happened, said in a toast with an Undo. The one place for the wording, how long it stays
// (longer than a plain toast, so there's time to read it and reach for it) and what happens when undoing fails.
import { toast } from "svelte-sonner";
import { errMsg } from "./act";

/** How long an Undo toast stays up, in ms. */
export const UNDO_MS = 10_000;

/**
 * Show `message` with an Undo button that runs `undo`. When it finishes the toast says "Undone" (with the text `undo`
 * returns, if it returns any); if it throws, the error is shown instead, so `undo` needs no catch of its own.
 * `also`: another way to take what just happened further, as the toast's main button beside Undo ("From now on" after
 * changing one date's amount); an error from it is shown the same way.
 */
export function undoable(message: string, undo: () => Promise<string | void>,
  opts: { description?: string; duration?: number; also?: { label: string; run: () => Promise<void> } } = {}): void {
  const undoButton = {
    label: "Undo",
    onClick: async () => {
      try {
        const what = await undo();
        toast("Undone", what ? { description: what } : undefined);
      } catch (err) { toast.error(errMsg(err) || "Couldn’t undo that"); }
    },
  };
  const also = opts.also;
  toast(message, {
    description: opts.description,
    duration: opts.duration ?? UNDO_MS,
    ...(also ? {
      cancel: undoButton,
      action: { label: also.label, onClick: async () => { try { await also.run(); } catch (err) { toast.error(errMsg(err) || "Couldn’t do that"); } } },
    } : { action: undoButton }),
  });
}
