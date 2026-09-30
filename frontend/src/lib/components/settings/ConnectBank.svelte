<script lang="ts">
  import { Button, type ButtonVariant } from "$lib/components/ui/button";
  import * as Sheet from "$lib/components/ui/sheet";
  import { connectPlaid } from "./plaid.svelte";
  import SimpleFinSetup from "./SimpleFinSetup.svelte";
  import { helpCls, linkCls } from "./ui";

  // The one way to add a connection: "Connect a bank" asks which way. SimpleFIN goes to its setup-token form; Plaid offers
  // a bank or card, or an investment account, once its API keys are set (otherwise it says so and opens the keys).
  let { configured, onkeys, variant = "default" }: { configured: boolean; onkeys: () => void; variant?: ButtonVariant } = $props();

  let open = $state(false);
  let step = $state<"choose" | "simplefin" | "plaid">("choose");
  let connecting = $state("");

  async function plaid(kind: string) {
    connecting = kind;
    open = false;
    await connectPlaid(kind);
    connecting = "";
  }
  function keys() { open = false; onkeys(); }

  const choiceCls = "flex w-full cursor-pointer flex-col items-start gap-0.5 rounded-xl border px-4 py-3 text-left text-sm outline-none transition-colors hover:bg-muted/50 focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50";
</script>

<Button {variant} disabled={!!connecting} onclick={() => { step = "choose"; open = true; }}>Connect a bank</Button>

<Sheet.Root bind:open>
  <Sheet.Content>
    <Sheet.Header>
      <Sheet.Title>{step === "simplefin" ? "Connect with SimpleFIN" : step === "plaid" ? "Connect with Plaid" : "Connect a bank"}</Sheet.Title>
      <Sheet.Description>{step === "choose" ? "How do you want to connect?" : step === "simplefin" ? "Paste a setup token; Runway claims it and runs the first sync." : "What kind of account is it?"}</Sheet.Description>
    </Sheet.Header>
    <div class="flex flex-col gap-3 px-4 pb-4">
      {#if step === "choose"}
        <button type="button" class={choiceCls} onclick={() => (step = "simplefin")}>
          <span class="font-medium">SimpleFIN</span>
          <span class="text-muted-foreground">Your banks through SimpleFIN Bridge, with a setup token.</span>
        </button>
        <button type="button" class={choiceCls} onclick={() => (step = "plaid")}>
          <span class="font-medium">Plaid</span>
          <span class="text-muted-foreground">Banks, credit cards (with statements) and investment accounts, through Link.</span>
        </button>
      {:else if step === "simplefin"}
        <p class={helpCls}>Paste a setup token from <a class={linkCls} href="https://beta-bridge.simplefin.org" target="_blank" rel="noopener">SimpleFIN Bridge</a>.</p>
        <SimpleFinSetup ondone={() => (open = false)} />
        <Button variant="ghost" size="sm" class="self-start" onclick={() => (step = "choose")}>Back</Button>
      {:else if configured}
        <button type="button" class={choiceCls} onclick={() => plaid("bank")}>
          <span class="font-medium">Bank or card account</span>
          <span class="text-muted-foreground">Checking, savings and credit cards, with up to two years of history.</span>
        </button>
        <button type="button" class={choiceCls} onclick={() => plaid("investments")}>
          <span class="font-medium">Investment account</span>
          <span class="text-muted-foreground">Brokerages and retirement accounts: holdings and activity.</span>
        </button>
        <Button variant="ghost" size="sm" class="self-start" onclick={() => (step = "choose")}>Back</Button>
      {:else}
        <p class={helpCls}>Plaid needs API keys first: a client ID and secret from your Plaid dashboard.</p>
        <Button class="self-start" onclick={keys}>Add Plaid API keys</Button>
        <Button variant="ghost" size="sm" class="self-start" onclick={() => (step = "choose")}>Back</Button>
      {/if}
    </div>
  </Sheet.Content>
</Sheet.Root>
