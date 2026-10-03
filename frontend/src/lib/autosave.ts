// Autosave, as in the classic app: `use:autosave={save}` on an input, select or checkbox runs `save` whenever its
// value changes (text fields when you leave them or press Enter), then flashes a small "Saved ✓" on it (or on the
// <label> around it; a checkbox's says it beside its text, so it can't cover the row above) and tells a screen reader.
// A failed save shows the error, marks the field invalid and leaves it as you typed it, with a "Not saved · Retry"
// line under it until a save goes through.
import { toast } from "svelte-sonner";

type Field = HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement;
const valueOf = (f: Field) => (f instanceof HTMLInputElement && f.type === "checkbox" ? f.checked : f.value);
const isCheck = (f: Element) => f instanceof HTMLInputElement && (f.type === "checkbox" || f.type === "radio");
const hostOf = (f: Element) => (f.closest("label") || f.parentElement || f) as HTMLElement;

// The "Not saved · Retry" line of each field that has one.
const failed = new WeakMap<Element, HTMLElement>();
let ids = 0;

function showFailure(f: Field, retry: () => void): void {
  f.setAttribute("aria-invalid", "true");
  const host = hostOf(f);
  host.classList.remove("just-saved");   // an earlier save's flash, still showing, would contradict it
  host.querySelector(":scope > [data-saved-flash]")?.remove();
  if (failed.has(f)) return;
  const line = document.createElement("span");
  line.id = `autosave-error-${++ids}`;
  line.dataset.autosaveError = "";
  line.className = "block text-xs font-normal text-destructive";
  line.append("Not saved · ");
  const again = document.createElement("button");
  again.type = "button";
  again.className = "font-medium underline underline-offset-4";
  again.textContent = "Retry";
  again.addEventListener("click", (e) => { e.preventDefault(); retry(); });
  line.append(again);
  // Under the field: inside its label (a caption above, the field below), or after a checkbox's whole row.
  if (isCheck(f) || host === f) (host === f ? f : host).after(line); else host.append(line);
  f.setAttribute("aria-describedby", [f.getAttribute("aria-describedby"), line.id].filter(Boolean).join(" "));
  failed.set(f, line);
}

function clearFailure(f: Element): void {
  const line = failed.get(f);
  if (!line) return;
  line.remove();
  failed.delete(f);
  f.removeAttribute("aria-invalid");
  const rest = (f.getAttribute("aria-describedby") ?? "").split(" ").filter((id) => id && id !== line.id).join(" ");
  if (rest) f.setAttribute("aria-describedby", rest); else f.removeAttribute("aria-describedby");
}

// One polite live region for the whole page, made before the first save so that save is read out too.
function announcer(): HTMLElement {
  let el = document.querySelector<HTMLElement>("body > [data-autosave-status]");
  if (!el) {
    el = document.createElement("span");
    el.dataset.autosaveStatus = "";
    el.setAttribute("role", "status");
    el.className = "sr-only";
    document.body.append(el);
  }
  return el;
}

export function autosave(f: Field, save: (f: Field) => unknown | Promise<unknown>) {
  announcer();
  let last = valueOf(f);
  let run = save;
  const onChange = async () => {
    const now = valueOf(f);
    if (now === last) return;
    try { await run(f); last = now; markSaved(f); }
    catch (err) { toast.error((err as Error).message); showFailure(f, onChange); }
  };
  const onKey = (e: Event) => {
    if ((e as KeyboardEvent).key === "Enter" && f instanceof HTMLInputElement) { e.preventDefault(); f.blur(); }
  };
  f.addEventListener("change", onChange);
  if (f instanceof HTMLInputElement && f.type !== "checkbox") f.addEventListener("keydown", onKey);
  return {
    update(next: typeof save) { run = next; },
    destroy() { f.removeEventListener("change", onChange); f.removeEventListener("keydown", onKey); clearFailure(f); },
  };
}

/** Flash "Saved ✓" on a field (or the label around it), say so to a screen reader, and clear a "Not saved" line. */
export function markSaved(f: Element): void {
  clearFailure(f);
  const host = hostOf(f);
  const said = announcer();
  said.textContent = "Saved";
  let flash: HTMLElement | null = null;
  if (isCheck(f)) {
    // Beside the checkbox's text: above it, as on a text field, it would cover the row before.
    host.querySelector(":scope > [data-saved-flash]")?.remove();
    flash = document.createElement("span");
    flash.dataset.savedFlash = "";
    flash.setAttribute("aria-hidden", "true");
    flash.className = "shrink-0 text-xs font-normal whitespace-nowrap text-good";
    flash.textContent = "Saved ✓";
    host.append(flash);
  } else {
    host.classList.remove("just-saved");
    void host.offsetWidth;
    host.classList.add("just-saved");
  }
  setTimeout(() => {
    host.classList.remove("just-saved");
    flash?.remove();
    if (said.textContent === "Saved") said.textContent = "";
  }, 1600);
}
