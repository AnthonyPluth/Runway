<script lang="ts">
  import { lock, lockScreenShown, unlock } from "$lib/lock.svelte";
  import { signOut } from "$lib/nav.svelte";
  import { Button } from "$lib/components/ui/button";
  import Lock from "@lucide/svelte/icons/lock";
  import ScanFace from "@lucide/svelte/icons/scan-face";
  import { onMount } from "svelte";

  // The app lock's screen (lib/lock.svelte.ts): all App draws while Runway is locked on this device, so nothing of
  // yours is on the page (or was asked for) until it's unlocked. The device's own prompt is offered straight away when
  // the screen is up and on top (some browsers want a tap first: then the button), and Sign out is always the way out
  // when Face ID or Touch ID won't work: it ends this sign-in, and the lock with it.
  let tried = false;
  async function offer() {
    if (tried || lock.busy || document.visibilityState !== "visible") return;
    tried = true;
    if (await lockScreenShown()) await unlock(true);
  }
  onMount(() => { if (lock.offer) void offer(); });
  function back() { if (document.visibilityState === "visible") { tried = false; void offer(); } }
</script>

<svelte:document onvisibilitychange={back} />

<main class="flex min-h-dvh flex-col items-center justify-center gap-6 px-6 pt-[env(safe-area-inset-top)] pb-[env(safe-area-inset-bottom)] text-center" aria-labelledby="lock-title">
  <img src="/logo.svg" width="56" height="56" alt="" />
  <div class="flex flex-col items-center gap-2">
    <h1 id="lock-title" class="flex items-center gap-2 text-2xl font-bold tracking-tight"><Lock class="size-5" aria-hidden="true" />Runway is locked</h1>
    <p class="max-w-xs text-sm text-muted-foreground">Unlock with Face ID, Touch ID or this device’s passcode.</p>
  </div>
  <Button size="lg" class="min-w-48" disabled={lock.busy} onclick={() => unlock()}>
    <ScanFace class="size-5" aria-hidden="true" />{lock.busy ? "Unlocking…" : "Unlock"}
  </Button>
  {#if lock.error}<p role="alert" class="max-w-xs text-sm text-destructive">{lock.error}</p>{/if}
  <a href="/auth/logout" onclick={signOut} class="text-sm text-muted-foreground underline underline-offset-4">Can’t unlock? Sign out</a>
</main>
