// The attributes a form field gets for its note: marked invalid and described by it (the required star is for sighted
// readers, so a required field says so with aria-required instead).
import type { FormErrors } from "./validate";

export function fieldProps(errors: FormErrors, uid: string, name: string, required = false) {
  const bad = !!errors[name];
  return {
    "aria-invalid": bad ? ("true" as const) : undefined,
    "aria-describedby": bad ? `${uid}-${name}-err` : undefined,
    "aria-required": required ? ("true" as const) : undefined,
  };
}

/** Focus the first field marked invalid inside a form, once the DOM has shown the marks. */
export function focusFirstInvalid(box: HTMLElement | null) {
  box?.querySelector<HTMLElement>("[aria-invalid=true]")?.focus();
}
