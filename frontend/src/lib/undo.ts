// A change that has already happened, said in a toast with an Undo. The one place for the wording, how long it stays
// (longer than a plain toast, so there's time to read it and reach for it) and what happens when undoing fails.
import { toast } from "svelte-sonner";

/** How long an Undo toast stays up, in ms. */
export const UNDO_MS = 10_000;

/**
 * Show `message` with an Undo button that runs `undo`. When it finishes the toast says "Undone" (with the text `undo`
 * returns, if it returns any); if it throws, the error is shown instead, so `undo` needs no catch of its own.
 */
export function undoable(message: string, undo: () => Promise<string | void>, opts: { description?: string; duration?: number } = {}): void {
  toast(message, {
    description: opts.description,
    duration: opts.duration ?? UNDO_MS,
    action: {
      label: "Undo",
      onClick: async () => {
        try {
          const what = await undo();
          toast("Undone", what ? { description: what } : undefined);
        } catch (err) { toast.error((err as Error).message || "Couldn’t undo that"); }
      },
    },
  });
}
