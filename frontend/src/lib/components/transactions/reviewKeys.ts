// The keys of To review: j / k (or ↓ / ↑) move between rows, Enter accepts the row's category (or opens the picker when
// it has none), c opens the picker, i marks it Ignore, t Transfer, s opens the split editor, Escape lets go of the row.
// Nothing happens while you're typing (a search box, the picker's own search), with a modifier held, or in a dialog.

export type ReviewAction = "next" | "prev" | "enter" | "pick" | "ignore" | "transfer" | "split" | "clear";

const KEYS: Record<string, ReviewAction> = {
  j: "next", ArrowDown: "next", k: "prev", ArrowUp: "prev", Enter: "enter", c: "pick", i: "ignore", t: "transfer", s: "split",
  Escape: "clear",
};

/** Whether the element takes typing (so its keys are its own). */
const typing = (el: Element) => el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement ||
  (el instanceof HTMLElement && el.isContentEditable);

/** What a key press does in Review, or null when it isn't Review's to handle. `onRow`: a row has the keyboard. */
export function reviewAction(e: Pick<KeyboardEvent, "key" | "target" | "ctrlKey" | "metaKey" | "altKey" | "defaultPrevented">, onRow: boolean): ReviewAction | null {
  if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey) return null;
  const action = KEYS[e.key.length === 1 ? e.key.toLowerCase() : e.key];
  if (!action) return null;
  const el = e.target instanceof Element ? e.target : null;
  if (el && (typing(el) || el.closest("[role=dialog], [role=alertdialog], [role=listbox], [data-sonner-toaster], [data-editor]"))) return null;
  // On a button or link, Enter and the arrows are its own (unless it's the row that has the keyboard).
  const control = el?.closest("button, a, [role=combobox], [role=checkbox], input");
  if (control && (action === "enter" || e.key.startsWith("Arrow"))) return null;
  // Without a row, only moving onto one means anything.
  if (!onRow && action !== "next" && action !== "prev") return null;
  return action;
}
