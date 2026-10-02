// Dollar fields with their commas: `{@attach commas}` on an <input> (or an <Input>) shows "450,000" while you're not in
// it, which a number field can't, and is a number field again while you edit it (its arrows, a numeric keyboard). Its
// `value` never has the commas, read or written: whatever reads the field (autosave, oninput, bind:value) still gets
// "450000", and a value set while you're elsewhere is shown with its commas.

const native = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!;

/** "1234567.5" → "1,234,567.5": commas in the whole-number part, the rest as it is (anything else unchanged). */
export const withCommas = (s: string) => s.replace(/^(-?)(\d+)/, (_, sign: string, int: string) => sign + int.replace(/\B(?=(\d{3})+(?!\d))/g, ","));
export const withoutCommas = (s: string) => s.replace(/,/g, "");

export function commas(el: HTMLInputElement) {
  const raw = () => withoutCommas(native.get!.call(el));
  const focused = () => el.ownerDocument.activeElement === el;
  // Changing the type clears a value the new type can't hold, so each switch reads it first and puts it back.
  const show = () => { const v = raw(); el.type = "text"; native.set!.call(el, withCommas(v)); };
  const edit = () => { const v = raw(); el.type = "number"; native.set!.call(el, v); };
  if (!el.inputMode) el.inputMode = "decimal";   // a phone's number keys before it becomes a number field
  Object.defineProperty(el, "value", {
    configurable: true,
    get: raw,
    set(v: unknown) { const s = withoutCommas(String(v ?? "")); native.set!.call(el, focused() ? s : withCommas(s)); },
  });
  el.addEventListener("focus", edit);
  el.addEventListener("blur", show);
  if (!focused()) show();
  return () => {
    el.removeEventListener("focus", edit);
    el.removeEventListener("blur", show);
    const v = raw();
    delete (el as { value?: string }).value;
    el.type = "number";
    el.value = v;
  };
}
