<script lang="ts">
  import { autosave } from "$lib/autosave";
  import { cn } from "$lib/utils";
  import Eye from "@lucide/svelte/icons/eye";
  import EyeOff from "@lucide/svelte/icons/eye-off";
  import { fieldCls, inputCls } from "./ui";

  // A key or secret, labelled: hidden as you type, with a button to show it, and never offered to (or filled from) the
  // browser's saved passwords. `save` autosaves it as other Settings fields do; without one, a form's Save button does.
  let { label, value = $bindable(""), placeholder = "", save, optional = false, class: cls = "" }: {
    label: string; value?: string; placeholder?: string; optional?: boolean; class?: string;
    save?: (f: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement) => unknown;
  } = $props();
  const id = $props.id();
  let shown = $state(false);
  const input = $derived(cn(inputCls, "w-full pr-10"));
</script>

<div class={cn(fieldCls, cls)}>
  <label for={id}>{label}{#if optional}{" "}<span class="font-normal text-muted-foreground">(optional)</span>{/if}</label>
  <span class="relative block">
    {#if save}
      <input {id} class={input} type={shown ? "text" : "password"} autocomplete="new-password" autocapitalize="off" spellcheck="false"
        {placeholder} bind:value use:autosave={save} />
    {:else}
      <input {id} class={input} type={shown ? "text" : "password"} autocomplete="new-password" autocapitalize="off" spellcheck="false"
        {placeholder} bind:value />
    {/if}
    <button type="button" class="absolute top-0 right-0 flex h-9 w-9 items-center justify-center rounded-lg text-muted-foreground hoverable:hover:text-foreground"
      aria-label={shown ? `Hide ${label}` : `Show ${label}`} aria-pressed={shown} aria-controls={id} onclick={() => (shown = !shown)}>
      {#if shown}<EyeOff class="size-4" aria-hidden="true" />{:else}<Eye class="size-4" aria-hidden="true" />{/if}
    </button>
  </span>
</div>
