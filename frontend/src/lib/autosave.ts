// Autosave, as in the classic app: `use:autosave={save}` on an input, select or checkbox runs `save` whenever its
// value changes (text fields when you leave them or press Enter), then flashes a small "Saved ✓" on it (or on the
// <label> around it). A failed save shows the error and leaves the field as you typed it, so you can try again.
import { toast } from "svelte-sonner";

type Field = HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement;
const valueOf = (f: Field) => (f instanceof HTMLInputElement && f.type === "checkbox" ? f.checked : f.value);

export function autosave(f: Field, save: (f: Field) => unknown | Promise<unknown>) {
  let last = valueOf(f);
  let run = save;
  const onChange = async () => {
    const now = valueOf(f);
    if (now === last) return;
    try { await run(f); last = now; markSaved(f); }
    catch (err) { toast.error((err as Error).message); }
  };
  const onKey = (e: Event) => {
    if ((e as KeyboardEvent).key === "Enter" && f instanceof HTMLInputElement) { e.preventDefault(); f.blur(); }
  };
  f.addEventListener("change", onChange);
  if (f instanceof HTMLInputElement && f.type !== "checkbox") f.addEventListener("keydown", onKey);
  return {
    update(next: typeof save) { run = next; },
    destroy() { f.removeEventListener("change", onChange); f.removeEventListener("keydown", onKey); },
  };
}

/** Flash "Saved ✓" on a field (or the label around it). */
export function markSaved(f: Element): void {
  const host = f.closest("label") || f.parentElement || f;
  host.classList.remove("just-saved");
  void (host as HTMLElement).offsetWidth;
  host.classList.add("just-saved");
  setTimeout(() => host.classList.remove("just-saved"), 1600);
}
