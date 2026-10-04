// What Churning's Add forms (a card, a bank bonus, a planned item, a benefit) share: checking the fields before
// anything is sent, adding, and showing a refusal in the section it's about.
import { act } from "$lib/act";
import { tick } from "svelte";
import { focusFirstInvalid } from "./form";
import type { FormErrors } from "./validate";

/** `validate` reads the form's values and says what's wrong with them. `sections` says which collapsible section a
 * refusal belongs to, from the words in the server's message (the first match wins); a form without sections leaves it
 * out, and a refusal shows only in its footer. */
export class AddForm<K extends string = never> {
  private validate: () => FormErrors = () => ({});
  private sections: [K, RegExp][] = [];
  /** After the first Add (or a refused save), the fields that need fixing say so, each with its own note. */
  attempted = $state(false);
  /** The server's refusal of the last Add, for the footer and the section it's about. */
  error = $state("");
  busy = $state(false);
  /** The section the refusal is about, which is open. */
  flagged = $state<K | null>(null);
  open = $state<Record<K, boolean>>({} as Record<K, boolean>);
  errors = $derived<FormErrors>(this.attempted ? this.validate() : {});

  constructor(validate: () => FormErrors, sections: [K, RegExp][] = []) {
    this.validate = validate; this.sections = sections;
    for (const [k] of sections) this.open[k] = false;
  }

  sectionOf = (msg: string): K | null => this.sections.find(([, re]) => re.test(msg))?.[0] ?? null;

  /** Validates and, if all is well, runs `send`; a refusal lands in `error` (and `flagged`, with its section opened). */
  async add(send: () => Promise<unknown>, box: HTMLElement | null): Promise<void> {
    if (this.busy) return;
    this.attempted = true; this.error = ""; this.flagged = null;
    await tick();
    if (Object.keys(this.validate()).length) { focusFirstInvalid(box); return; }
    await act(send, {
      busy: (on) => { this.busy = on; },
      onError: (message) => {
        this.error = message;
        this.flagged = this.sectionOf(message);
        if (this.flagged) this.open[this.flagged] = true;
      },
    });
  }
}
