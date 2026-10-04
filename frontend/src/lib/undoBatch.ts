// Changes made one after another (categorizing transaction after transaction in Review) share one Undo toast instead of
// each pushing the last one away: "3 changed · Undo" puts all of them back, newest first. A change made after the toast
// has gone starts a new one. The toast stays while the pointer is over it (the toaster's own) or a key is on it.
import { toast } from "svelte-sonner";
import { UNDO_MS } from "./undo";
import { errMsg } from "./act";

type Undo = () => Promise<string | void>;
export interface Also { label: string; run: () => Promise<void> }
interface Batch { id: string; undos: Undo[]; message: string; description?: string; also?: Also; paused: boolean }

let current: Batch | null = null;
let seq = 0;

function show(b: Batch): void {
  const n = b.undos.length;
  const undoButton = {
    label: "Undo",
    onClick: async () => {
      if (current === b) current = null;   // undone: the next change starts afresh
      try {
        let what: string | void = undefined;
        for (const u of [...b.undos].reverse()) what = await u();
        toast("Undone", n > 1 ? { description: `${n} changes` } : what ? { description: what } : undefined);
      } catch (err) { toast.error(errMsg(err) || "Couldn’t undo that"); }
    },
  };
  const end = () => { if (current === b) current = null; };
  toast(n > 1 ? `${n} changed` : b.message, {
    id: b.id,
    description: n > 1 ? b.message : b.description,
    duration: b.paused ? Number.POSITIVE_INFINITY : UNDO_MS,
    onDismiss: end,
    onAutoClose: end,
    ...(b.also ? {
      cancel: undoButton,
      action: { label: b.also.label, onClick: async () => { try { await b.also!.run(); } catch (err) { toast.error(errMsg(err) || "Couldn’t do that"); } } },
    } : { action: undoButton }),
  });
}

/**
 * As `undoable`, but a change made while the last one's toast is still up joins it: one toast, one Undo for all of
 * them. `also` (the toast's main button, as in `undoable`) belongs to the newest change.
 */
export function undoBatched(message: string, undo: Undo, opts: { description?: string; also?: Also } = {}): void {
  if (current) {
    current.undos.push(undo);
    current.message = message; current.description = opts.description; current.also = opts.also;
  } else current = { id: `undo-${++seq}`, undos: [undo], message, description: opts.description, also: opts.also, paused: false };
  show(current);
}

/** Forget the toast that's up (a new list, say), so the next change starts its own. */
export function endBatch(): void { current = null; }

// A keyboard user reading the toast (Tab into it) gets as long as they need, as a pointer over it does.
const inToast = (n: EventTarget | null) => n instanceof Element && !!n.closest("[data-sonner-toast]");
if (typeof document !== "undefined") {
  document.addEventListener("focusin", (e) => {
    if (current && !current.paused && inToast(e.target)) { current.paused = true; show(current); }
  });
  document.addEventListener("focusout", (e) => {
    if (current?.paused && inToast(e.target) && !inToast(e.relatedTarget)) { current.paused = false; show(current); }
  });
}
