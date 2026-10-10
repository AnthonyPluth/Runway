<script lang="ts">
  import { act, errMsg } from "$lib/act";
  import { apiCall } from "$lib/contract";
  import type { LockStatus } from "$lib/api-types";
  import { Button } from "$lib/components/ui/button";
  import Group from "$lib/components/ui/group/Group.svelte";
  import { IDLE_CHOICES, deviceUserId, lock, lockNow, readSaved, turnedOff, turnedOn } from "$lib/lock.svelte";
  import { createPasskey, unsupportedReason, webauthnError } from "$lib/webauthn";
  import { toast } from "svelte-sonner";
  import { fieldCls, helpCls, selectCls } from "./ui";

  // Settings → Data → App lock: Face ID, Touch ID or the passcode before Runway shows anything, on this device only
  // (lib/lock.svelte.ts, runway/applock.py). Each device turns it on for itself; nothing here is shared with your other
  // devices. A device that can't have one says why, and nothing can lock you out: signing out always gets you out.
  let status = $state<LockStatus | null>(null);
  let loadError = $state("");
  let unsupported = $state<string | null>(null);   // null: still finding out; "": this device can
  let idle = $state(readSaved().idle);
  let busy = $state(false);

  async function load() {
    try {
      status = await apiCall<"GET /api/lock">("/api/lock");
      loadError = "";
      if (status.on) { idle = status.idle; turnedOn(status); }
      else if (readSaved().on) turnedOff();   // the server has none for this sign-in: nor does the device
    } catch (err) { loadError = errMsg(err); }
  }
  load();
  unsupportedReason().then((r) => (unsupported = r), () => (unsupported = "device"));

  async function turnOn() {
    busy = true;
    try {
      const ch = await apiCall<"POST /api/lock/challenge">("/api/lock/challenge", { method: "POST", body: { purpose: "register" } });
      let made;
      try { made = await createPasskey(ch, deviceUserId()); }
      catch (err) { toast.error(webauthnError(err)); return; }
      status = await apiCall<"POST /api/lock/register">("/api/lock/register", { method: "POST", body: { ...made, idle } });
      turnedOn(status);
      toast.success("App lock is on for this device");
    } catch (err) { toast.error(errMsg(err)); }
    finally { busy = false; }
  }
  async function setIdle(e: Event) {
    const sel = e.currentTarget as HTMLSelectElement, was = idle;
    idle = Number(sel.value);
    if (!status?.on) return;   // kept for when it's turned on
    const ok = await act(async () => {
      status = await apiCall<"POST /api/lock/settings">("/api/lock/settings", { method: "POST", body: { idle } });
      turnedOn(status);
    });
    if (!ok) { idle = was; sel.value = String(was); }   // as it was: nothing changed
  }
  async function turnOff() {
    await act(async () => {
      status = await apiCall<"DELETE /api/lock">("/api/lock", { method: "DELETE" });
      turnedOff();
      toast.success("App lock is off for this device");
    }, { busy: (on) => (busy = on) });
  }
  const b = "font-medium text-foreground";
</script>

<Group title="App lock" footer="This device only. It keeps someone holding your unlocked phone or computer out of Runway; it doesn’t replace signing in. Signing out turns it off.">
  <div class="cell flex-col items-start gap-3 py-3 text-sm leading-relaxed">
    {#if loadError}
      <p class="text-muted-foreground">{loadError}</p>
    {:else if !status || unsupported === null}
      <p class="text-muted-foreground">Loading…</p>
    {:else if !status.available}
      <p>App lock needs Runway’s sign-in (<code class="rounded bg-muted px-1">OIDC_ISSUER</code> and the rest): without it there’s no sign-in on this device for it to lock.</p>
    {:else if unsupported === "https"}
      <p>App lock needs Runway to be opened over <b class={b}>https://</b> (your <code class="rounded bg-muted px-1">RUNWAY_PUBLIC_URL</code>). It can’t be turned on from this address.</p>
    {:else if unsupported && !status.on}
      <p>This {unsupported === "browser" ? "browser" : "device"} can’t use Face ID, Touch ID or a passcode for websites, so app lock can’t be turned on here. On an iPhone or iPad use Safari or the Home Screen app (iOS 16 or later); on a computer, a current Chrome, Edge, Safari or Firefox with Touch ID, Windows Hello or similar set up.</p>
    {:else}
      <p>Ask for Face ID, Touch ID or this device’s passcode before Runway shows anything: when you open it, and when you come back to it after it’s been in the background. While it’s locked, nothing of yours is on the screen or sent to this device.</p>
      {#if status.on}
        <p class="flex items-center gap-2"><span class="inline-block size-2 rounded-full bg-(--good)" aria-hidden="true"></span><b class={b}>On for this device</b></p>
      {/if}
      <label class={fieldCls}>Lock again after it’s been in the background
        <select class={`${selectCls} w-56`} value={String(idle)} onchange={setIdle} disabled={busy}>
          {#each IDLE_CHOICES as c (c.seconds)}<option value={String(c.seconds)}>{c.label}</option>{/each}
        </select>
      </label>
      <div class="flex flex-wrap gap-2">
        {#if status.on}
          <Button variant="outline" disabled={busy || lock.phase !== "unlocked"} onclick={() => lockNow(true, false)}>Lock now</Button>
          <Button variant="outline" disabled={busy} onclick={turnOff}>Turn off on this device</Button>
        {:else}
          <Button disabled={busy} onclick={turnOn}>{busy ? "Waiting for the device…" : "Turn on app lock"}</Button>
        {/if}
      </div>
      <p class={helpCls}>If Face ID or Touch ID stops working, the lock screen’s <b class={b}>Sign out</b> gets you out (and turns the lock off); sign in again and turn it back on.</p>
    {/if}
  </div>
</Group>
