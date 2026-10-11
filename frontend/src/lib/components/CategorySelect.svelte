<script lang="ts">
  import { categories, categoryGroups } from "$lib/categories.svelte";
  import * as Sheet from "$lib/components/ui/sheet";
  import { viewport } from "$lib/phone.svelte";
  import type { Category } from "$lib/types";
  import { cn } from "$lib/utils";
  import type { Snippet } from "svelte";
  import { tick, untrack } from "svelte";
  import Check from "@lucide/svelte/icons/check";
  import ChevronDown from "@lucide/svelte/icons/chevron-down";
  import Search from "@lucide/svelte/icons/search";
  import { catParentName, pickSections, type PickOption } from "./categoryPicker";

  // Pick a category: a button showing it, which opens a list you can type into to narrow it down ("gro" for Groceries),
  // grouped as Spending / Money in / Not spending. Arrows move, Enter picks,
  // Escape closes, Tab moves on. On a phone the list comes up as a sheet from the bottom. The list is only built while
  // it's open, so a page of rows each with a picker stays light. Call loadCategories() before showing it.
  // `blank` adds a first "Choose…" option (value ""); `extra` puts options of your own before the groups.
  // Closed, the button leads with the emoji, then the name, then the parent as muted "in Travel" text; `short` leaves
  // the parent out ("Public Transit"). The open list leads each row with the emoji and indents a child under its parent;
  // typing flattens it, a child written "Child · Parent". The button's accessible name carries the choice: "Category
  // for Lyft: Public Transit, in Travel".
  // `repick`: picking the category it already has still counts (Review: keeping a suggested category accepts it).
  // `children`: what the closed button shows instead of the category's name (a row's chip).
  let { value = $bindable(""), blank = "Choose…", canHoldChildren = false, exclude, ghost = false, short = false,
    label = "Category", class: className, disabled = false, onchange, extra, repick = false, children }: {
    value?: string; blank?: string | false; canHoldChildren?: boolean; exclude?: (c: Category) => boolean; ghost?: boolean;
    short?: boolean; label?: string; class?: string; disabled?: boolean; onchange?: (value: string) => void;
    extra?: PickOption[]; repick?: boolean; children?: Snippet;
  } = $props();

  const uid = $props.id();
  let open = $state(false);
  let query = $state("");
  let active = $state(0);
  let trigger = $state<HTMLButtonElement>();
  let input = $state<HTMLInputElement>();
  let listbox = $state<HTMLElement>();
  let panel = $state<HTMLElement>();

  // The desktop list floats under the button (over it when there isn't room below), inside the window. While it's
  // open it follows the button as the page scrolls, and a click anywhere else closes it.
  let pos = $state({ top: 0, left: 0, width: 256 });
  function place() {
    if (!trigger) return;
    const r = trigger.getBoundingClientRect(), h = panel?.offsetHeight ?? 320;
    const width = Math.min(Math.max(256, r.width), window.innerWidth - 16);
    const below = r.bottom + 4, above = r.top - 4 - h;
    const top = below + h > window.innerHeight - 8 && above > 8 ? above : below, left = Math.max(8, Math.min(r.left, window.innerWidth - width - 8));
    // Inside something moved by a transform (a side panel sliding in), "fixed" moves with it: take off how far off the
    // panel actually is.
    const was = untrack(() => pos), p = panel?.getBoundingClientRect(), dx = p ? p.left - was.left : 0, dy = p ? p.top - was.top : 0;
    pos = { top: top - dy, left: left - dx, width };
  }
  $effect(() => {
    if (!open || viewport.phone) return;
    place();
    // Again each frame for a moment: the panel's own size, and anything around it still sliding into place.
    const began = performance.now();
    let frame = requestAnimationFrame(function again() { place(); if (performance.now() - began < 500) frame = requestAnimationFrame(again); });
    const outside = (e: PointerEvent) => {
      const t = e.target as Node;
      if (!panel?.contains(t) && !trigger?.contains(t)) close(false);
    };
    document.addEventListener("pointerdown", outside, true);
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener("pointerdown", outside, true);
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
    };
  });

  // What the closed button says.
  const current = $derived(categories.list.find((c) => c.name === value));
  const shown = $derived(value === "" ? (blank === false ? "" : blank)
    : extra?.find((o) => o.value === value)?.label ?? current?.name ?? value);
  const icon = $derived(current?.icon);
  const parent = $derived(catParentName(current));
  const triggerName = $derived(current && value !== "" && !extra?.some((o) => o.value === value)
    ? `${label}: ${current.name}${parent ? `, in ${parent}` : ""}` : label);

  const sections = $derived(open ? pickSections({ groups: categoryGroups({ canHoldChildren, exclude }), query, blank, extra }) : []);
  const flat = $derived(sections.flatMap((s, si) => s.items.map((o) => ({ ...o, id: `${uid}-${si}-${o.value}`, section: s.label }))));
  const listId = `${uid}-list`;

  function show(typed = "") {
    if (disabled || open) return;
    query = typed; open = true;
    // Start on the category it has (or the first one), so Enter straight away keeps it.
    tick().then(() => {
      const at = typed ? 0 : flat.findIndex((o) => o.value === value);
      active = Math.max(0, at);
      input?.focus();
      scrollToActive();
    });
  }
  function close(refocus = true) {
    if (!open) return;
    open = false; query = "";
    if (refocus) trigger?.focus();
  }
  function pick(o: PickOption) {
    const changed = o.value !== value;
    value = o.value;
    close();
    if (changed || repick) onchange?.(o.value);
  }
  function move(by: number) {
    if (!flat.length) return;
    active = (active + by + flat.length) % flat.length;
    scrollToActive();
  }
  function scrollToActive() {
    tick().then(() => listbox?.querySelector(`[id="${CSS.escape(flat[active]?.id ?? "")}"]`)?.scrollIntoView({ block: "nearest" }));
  }

  function onTriggerKey(e: KeyboardEvent) {
    if (e.key === "ArrowDown" || e.key === "ArrowUp" || e.key === "Enter" || e.key === " ") { e.preventDefault(); show(); return; }
    // Typing on the closed picker starts the search with that letter.
    if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey && /\S/.test(e.key)) { e.preventDefault(); show(e.key); }
  }
  function onInputKey(e: KeyboardEvent) {
    if (e.key === "ArrowDown") { e.preventDefault(); move(1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
    else if (e.key === "Enter") { e.preventDefault(); const o = flat[active]; if (o) pick(o); }
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); }
    // Tab: closes, and focus goes on from the button (the browser does that part).
    else if (e.key === "Tab") close();
  }
  function oninput(e: Event) { query = (e.currentTarget as HTMLInputElement).value; active = 0; }
</script>

<button bind:this={trigger} type="button" role="combobox" aria-label={triggerName} aria-haspopup="listbox" aria-expanded={open}
  aria-controls={open ? listId : undefined} data-category-trigger data-value={value} {disabled}
  class={children ? cn("cursor-pointer outline-none disabled:cursor-not-allowed", className) : cn(
    "relative inline-flex h-9 min-w-0 cursor-pointer items-center gap-1.5 rounded-lg border border-transparent bg-transparent py-1 pr-8 pl-2.5 text-left text-base outline-none transition-[color,box-shadow] dark:bg-input md:text-sm",
    "focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50",
    ghost && "shadow-none dark:bg-transparent hover:border-input", className)}
  onclick={() => (open ? close() : show())} onkeydown={onTriggerKey}>
  {#if children}{@render children()}{:else}
    {#if icon}<span aria-hidden="true" class="shrink-0">{icon}</span>{/if}
    <span class={cn("min-w-0 truncate", value === "" && "text-muted-foreground")}>{shown}{#if parent && !short}<span class="text-muted-foreground">{` in ${parent}`}</span>{/if}</span>
    <ChevronDown aria-hidden="true" class="pointer-events-none absolute top-1/2 right-2.5 size-4 -translate-y-1/2 opacity-60" />
  {/if}
</button>

{#if open}
  {#if viewport.phone}
    <Sheet.Root bind:open={() => open, (o) => { if (!o) close(); }}>
      <Sheet.Content class="max-h-[85dvh] gap-0 p-0 pt-2" onOpenAutoFocus={(e) => { e.preventDefault(); input?.focus(); }}
        onCloseAutoFocus={(e) => e.preventDefault()}>
        <Sheet.Title class="px-4 pt-2 pr-12 text-base font-semibold">{label}</Sheet.Title>
        {@render picker()}
      </Sheet.Content>
    </Sheet.Root>
  {:else}
    <!-- In place (not moved to the end of the page), so a picker inside a dialog keeps focus inside it; fixed, so a
         list that clips its rows doesn't clip it. -->
    <div bind:this={panel} style:top={`${pos.top}px`} style:left={`${pos.left}px`} style:width={`${pos.width}px`}
      class="fixed z-[70] rounded-xl border bg-popover text-popover-foreground shadow-xl">
      {@render picker()}
    </div>
  {/if}
{/if}

{#snippet picker()}
  <div class="relative border-b">
    <Search class="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
    <!-- 16px text on a phone, so the browser doesn't zoom in on it. -->
    <input bind:this={input} type="text" role="combobox" aria-label="Search categories" aria-expanded="true" aria-controls={listId}
      aria-autocomplete="list" aria-activedescendant={flat[active]?.id} autocomplete="off" spellcheck="false" placeholder="Search"
      class="h-11 w-full bg-transparent pr-3 pl-9 text-base outline-none placeholder:text-muted-foreground md:h-10 md:text-sm"
      value={query} {oninput} onkeydown={onInputKey} />
  </div>
  <div bind:this={listbox} id={listId} role="listbox" aria-label={label} class="max-h-72 overflow-y-auto overscroll-contain p-1 phone:max-h-[60dvh]">
    {#each sections as s, si (s.label + si)}
      <div role="group" aria-labelledby={s.label ? `${uid}-g${si}` : undefined}>
        {#if s.label}<div id={`${uid}-g${si}`} class="px-2 pt-2 pb-1 text-xs font-medium text-muted-foreground">{s.label}</div>{/if}
        {#each s.items as o (o.value)}
          {@const id = `${uid}-${si}-${o.value}`}
          {@const at = flat.findIndex((x) => x.id === id)}
          <!-- svelte-ignore a11y_click_events_have_key_events, a11y_interactive_supports_focus -->
          <div {id} role="option" aria-selected={o.value === value} data-value={o.value}
            aria-label={o.name && o.parent ? `${o.name}, ${o.parent}` : undefined}
            style:padding-left={o.depth ? `${8 + o.depth * 26}px` : undefined}
            class={cn("flex min-h-9 cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm phone:min-h-11",
              at === active && "bg-muted", o.value === "" && "text-muted-foreground")}
            onpointerdown={(e) => e.preventDefault()} onpointermove={() => (active = at)} onclick={() => pick(o)}>
            {#if o.icon}<span aria-hidden="true" class="w-[1.25em] shrink-0 text-center">{o.icon}</span>{/if}
            <span class="min-w-0 flex-1 truncate">{o.label}</span>
            {#if o.value === value && o.value !== ""}<Check class="size-4 shrink-0 text-primary" aria-hidden="true" />{/if}
          </div>
        {/each}
      </div>
    {:else}
      <p class="px-2 py-3 text-sm text-muted-foreground">No category matches</p>
    {/each}
  </div>
{/snippet}
