<script lang="ts">
  import { commas } from "$lib/commas";
  import { api } from "$lib/api";
  import { autosave } from "$lib/autosave";
  import ConfirmButton from "$lib/components/ConfirmButton.svelte";
  import { Badge } from "$lib/components/ui/badge";
  import { Button } from "$lib/components/ui/button";
  import Group from "$lib/components/ui/group/Group.svelte";
  import { fmtDateTime } from "$lib/format";
  import { toast } from "svelte-sonner";
  import { b64uToBytes, currentSubscription, deviceName, isInstalled, isIOS, pushSupported } from "./push";
  import type { PushInfo } from "./types";
  import { checkCls, dangerGhost, inputCls } from "./ui";

  // Settings → Notifications: turn them on or off for this device, what to be told about, and the devices that get
  // them. With sign-in they're each person's own: your devices and choices, never anyone else's.
  type Loaded = { d: PushInfo; reg: ServiceWorkerRegistration | null; sub: PushSubscription | null };
  let data = $state<Promise<Loaded>>(load());
  async function load(): Promise<Loaded> {
    const d = await api<PushInfo>("/api/push", { keep: true });
    return { d, ...(await currentSubscription()) };
  }
  const again = () => { data = load(); };

  const supported = pushSupported();
  let busy = $state(false);

  async function turnOn(l: Loaded) {
    busy = true;
    try {
      // iOS only allows the permission prompt straight from a tap, so ask before anything else.
      const perm = await Notification.requestPermission();
      if (perm !== "granted") throw new Error(perm === "denied" ? "Notifications were blocked." : "Notifications weren't allowed.");
      const reg = l.reg || (await navigator.serviceWorker.register("/sw.js"));
      await navigator.serviceWorker.ready;
      const s = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64uToBytes(l.d.public_key) });
      await api("/api/push/subscribe", { method: "POST", body: { subscription: s.toJSON(), device: deviceName() } });
      toast.success("Notifications are on");
      await api("/api/push/test", { method: "POST", body: { endpoint: s.endpoint } }).catch(() => {});
    } catch (err) { toast.error((err as Error).message); }
    busy = false;
    again();
  }
  async function test(sub: PushSubscription) {
    try { await api("/api/push/test", { method: "POST", body: { endpoint: sub.endpoint } }); toast.success("Sent. It should arrive in a few seconds."); }
    catch (err) { toast.error((err as Error).message); }
    again();
  }
  async function turnOff(sub: PushSubscription) {
    try { await api("/api/push/unsubscribe", { method: "POST", body: { endpoint: sub.endpoint } }); await sub.unsubscribe(); toast.success("Notifications are off on this device"); }
    catch (err) { toast.error((err as Error).message); }
    again();
  }
  async function removeDevice(ep: string, sub: PushSubscription | null) {
    try {
      await api("/api/push/unsubscribe", { method: "POST", body: { endpoint: ep } });
      if (sub && sub.endpoint === ep) await sub.unsubscribe().catch(() => {});
      toast.success("Removed · notifications are off on that device");
    } catch (err) { toast.error((err as Error).message); }
    again();
  }
  const savePref = (k: string) => async (f: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement) => {
    await api("/api/push/prefs", { method: "POST", body: { [k]: f instanceof HTMLInputElement && f.type === "checkbox" ? f.checked : f.value } });
  };
  const when = (t: number, time = true) =>
    (time ? fmtDateTime(new Date(t * 1000)) : new Date(t * 1000).toLocaleString("en-US", { month: "short", day: "numeric" }));
  // `label` says what you'd be told about, on its own; `hint` is the full sentence, as a tooltip.
  const RULES: { k: string; label: string; hint?: string; num?: [string, string, string, number] }[] = [
    { k: "card_due", label: "Card payment due", hint: "A card payment is coming up", num: ["card_due_days", "", " days ahead", 1] },
    { k: "low_balance", label: "Low forecast balance", hint: "The forecast gets low in the next 30 days", num: ["low_balance_below", "below $", "", 50] },
    { k: "missed", label: "Missed recurring payment", hint: "A recurring payment didn't show up" },
    { k: "big_charge", label: "Large charge", hint: "A large charge posts", num: ["big_charge_over", "over $", "", 50] },
    { k: "review", label: "Transactions waiting for review", hint: "Transactions are waiting for a category (at most once a day)" },
    { k: "sync_failed", label: "Bank sync failing for a day", hint: "Syncing with your bank has been failing for a day" },
    { k: "churn_fee", label: "Card annual fee coming up", hint: "A churning card's annual fee is due within 30 days (unless you're keeping it or have a plan for it)" },
    { k: "churn_bonus", label: "Sign-up bonus deadline near", hint: "A card or bank bonus deadline is within 14 days, with requirements left" },
    { k: "churn_plan", label: "Time for a planned card change", hint: "It's time to downgrade, close or change a card, as you planned" },
    { k: "churn_benefit", label: "Unused card credit about to reset", hint: "A card credit with money left is about to reset" },
    { k: "churn_apply", label: "Planned bonus ready to apply for", hint: "A card or bank bonus you planned has nothing in the way now, or its offer ends within 14 days" },
  ];
  // A device's last failure, said plainly; the push service's own words are behind Details.
  const friendly = (e: string) => (/couldn't reach/i.test(e) ? "Couldn’t reach its push service last time." : "The last notification didn’t get through.");
  const b = "font-medium text-foreground";
</script>

{#await data}
  <Group title="This device"><p class="cell text-sm text-muted-foreground">Loading…</p></Group>
{:then l}
  {@const d = l.d}
  {@const sub = l.sub}
  {@const here = sub && d.devices.find((x) => x.endpoint === sub.endpoint)}
  {@const mine = here && !here.unclaimed ? here : null}
  <Group title="This device">
    <div class="cell flex-col items-start gap-3 py-3 text-sm leading-relaxed">
      {#if !window.isSecureContext}
        <p>Notifications need Runway to be opened over <b class={b}>https://</b> (your <code class="rounded bg-muted px-1">RUNWAY_PUBLIC_URL</code>). They can't be turned on from this address.</p>
      {:else if isIOS() && !isInstalled()}
        <p>On iPhone and iPad, notifications work once Runway is on your Home Screen (iOS 16.4 or later):</p>
        <ol class="list-decimal space-y-1 pl-5">
          <li>Tap the <b class={b}>Share</b> button <span class="text-muted-foreground">(the square with an arrow)</span> in Safari.</li>
          <li>Choose <b class={b}>Add to Home Screen</b>, then <b class={b}>Add</b>.</li>
          <li>Open Runway from the new icon, come back to <b class={b}>Settings → Notifications</b> and turn them on.</li>
        </ol>
      {:else if !supported}
        <p>This browser can't receive notifications. On iPhone, add Runway to the Home Screen from Safari; on a computer, use a current Chrome, Edge, Firefox or Safari.</p>
      {:else if Notification.permission === "denied"}
        <p>Notifications are blocked for Runway on this device.
          {#if isIOS()}Turn them on in the iPhone's <b class={b}>Settings → Notifications → Runway</b>{:else}Allow them in your browser's site settings for this address{/if}, then come back here.</p>
      {:else if mine && sub}
        <p class="flex items-center gap-2"><span class="inline-block size-2 rounded-full bg-(--good)" aria-hidden="true"></span><b class={b}>On for this device</b> ({mine.device}).</p>
        <div class="flex flex-wrap gap-2">
          <Button onclick={() => test(sub)}>Send a test notification</Button>
          <Button variant="outline" onclick={() => turnOff(sub)}>Turn off on this device</Button>
        </div>
      {:else}
        {#if here}<p>Notifications were turned on here before there was sign-in. Turn them on again to get yours.</p>{/if}
        <p>Get a notification on this {isIOS() ? (/iPad/.test(navigator.userAgent) ? "iPad" : "iPhone") : "device"} when something needs your attention.</p>
        <div><Button disabled={busy} onclick={() => turnOn(l)}>Turn on notifications</Button></div>
      {/if}
    </div>
  </Group>

  <Group title="What to tell you about">
    {#each RULES as r (r.k)}
      <div class="cell flex-wrap gap-x-4 gap-y-1 py-2">
        <label class={`${checkCls} min-h-8 items-center [&>input]:mt-0`} title={r.hint}><input type="checkbox" checked={!!d.prefs[r.k]} use:autosave={savePref(r.k)} /> {r.label}</label>
        {#if r.num}
          {@const [k, pre, post, step] = r.num}
          <!-- Fixed-width prefix and suffix so the inputs line up in one column down the list. -->
          <label class="flex w-full items-center gap-1 pl-6 text-sm text-muted-foreground sm:ml-auto sm:w-auto sm:pl-0"><span class="whitespace-nowrap sm:w-14 sm:text-right">{pre}</span><input class={`${inputCls} h-8 w-24`} type="number" min="0" {step}
            value={d.prefs[k]} {@attach pre.includes("$") ? commas : undefined} use:autosave={savePref(k)} aria-label={`${r.label}: ${pre}…${post}`.replace(": …", ": ")} /><span class="sm:w-20">{post}</span></label>
        {/if}
      </div>
    {/each}
  </Group>

  {#if d.devices.length}
    <Group title="Devices">
      {#each d.devices as x (x.endpoint)}
        <div class="cell flex-wrap items-start gap-y-1">
          <div class="flex min-w-0 flex-1 flex-col gap-0.5 text-sm">
            <span class="flex flex-wrap items-center gap-1.5">{x.device || "Device"}
              {#if sub && x.endpoint === sub.endpoint}<Badge variant="secondary">this one</Badge>{/if}
              {#if x.unclaimed}<Badge variant="outline" title="Turned on before there was sign-in: it gets nothing until it's turned on again from the device">from before sign-in</Badge>{/if}
            </span>
            <span class="text-xs text-muted-foreground">{x.created ? `Added ${when(x.created, false)} · ` : ""}{x.last_ok ? `last delivered ${when(x.last_ok)}` : "nothing delivered yet"}</span>
            {#if x.last_error}
              <div class="text-xs">
                <span class="text-destructive">{friendly(x.last_error)}</span>
                <details class="inline">
                  <summary class="inline cursor-pointer text-muted-foreground underline-offset-4 hover:underline phone:inline-flex phone:min-h-11 phone:items-center">Details</summary>
                  <code class="mt-1 block rounded bg-muted px-1.5 py-1 break-all text-muted-foreground">{x.last_error}</code>
                </details>
              </div>
            {/if}
          </div>
          <ConfirmButton confirm="Remove?" variant="ghost" class={`h-8 px-2.5 ${dangerGhost}`} title="Stop notifications on this device"
            onconfirm={() => removeDevice(x.endpoint, sub)}>Remove</ConfirmButton>
        </div>
      {/each}
    </Group>
  {/if}

  {#if d.recent.length}
    <Group title="Recently sent">
      {#each d.recent as r, i (i)}
        <div class="cell min-h-11 justify-between py-2 text-sm"><span class="min-w-0">{r.title}</span><span class="shrink-0 text-muted-foreground">{when(r.sent)}</span></div>
      {/each}
    </Group>
  {/if}
{:catch err}
  <Group title="Notifications">
    <div class="cell flex-col items-start gap-3 py-3">
      <p class="text-sm">Something went wrong: {err.message}</p>
      <Button variant="outline" onclick={again}>Try again</Button>
    </div>
  </Group>
{/await}
