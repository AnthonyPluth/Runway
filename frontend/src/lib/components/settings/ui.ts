// Settings is mostly forms, and its fields autosave (`use:autosave`), which needs the native element rather than a
// component: these are the ui/ Input and NativeSelect looks as plain classes, plus the few layouts every card repeats.
// Fields use 16px text on a phone, below which iOS zooms in on focus.
export const inputCls = "border-transparent bg-background dark:bg-input placeholder:text-muted-foreground text-foreground h-9 min-w-0 rounded-lg border px-3 py-1 text-base outline-none transition-[color,box-shadow] disabled:cursor-not-allowed disabled:opacity-50 md:text-sm focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px] aria-invalid:border-destructive";
export const selectCls = "border-transparent dark:bg-input text-foreground h-9 min-w-0 cursor-pointer rounded-lg border bg-transparent py-1 pl-2.5 pr-8 text-base md:text-sm outline-none transition-[color,box-shadow] focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive [&>optgroup]:bg-popover [&_option]:bg-popover";
/** A field's label: the caption above, the control below. */
export const fieldCls = "flex min-w-0 flex-col gap-1.5 text-sm text-muted-foreground";
/** A row of fields that wraps on a phone. */
export const rowCls = "flex flex-wrap items-end gap-3";
/** A checkbox with its text beside it; on a phone the whole row is a 44px target. */
export const checkCls = "flex w-fit cursor-pointer items-start gap-2 text-sm phone:min-h-11 phone:py-3 [&>input]:mt-0.5 [&>input]:size-4 [&>input]:shrink-0 [&>input]:cursor-pointer [&>input]:accent-primary";
/** A ghost button for something that removes or deletes: red text, a faint red wash on hover. */
export const dangerGhost = "text-destructive hover:bg-destructive/10 hover:text-destructive dark:hover:bg-destructive/15";
export const linkCls = "font-medium text-foreground underline underline-offset-4";
export const helpCls = "text-sm leading-relaxed text-muted-foreground";
/** Something that needs a look ("no paying account"): the classic app's orange. */
export const warnText = "text-(--low)";
