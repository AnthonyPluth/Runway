<script lang="ts">
  import { api } from "$lib/api";
  import { autosave } from "$lib/autosave";
  import ConfirmButton from "$lib/components/ConfirmButton.svelte";
  import { Badge } from "$lib/components/ui/badge";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { fmtDateTime } from "$lib/format";
  import { toast } from "svelte-sonner";
  import { b64uToBytes, currentSubscription, deviceName, isInstalled, isIOS, pushSupported } from "./push";
  import type { PushInfo } from "./types";
  import { checkCls, inputCls } from "./ui";

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
      toast.success("Notifications are off on that device");
    } catch (err) { toast.error((err as Error).message); }
    again();
  }
  const savePref = (k: string) => async (f: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement) => {
    await api("/api/push/prefs", { method: "POST", body: { [k]: f instanceof HTMLInputElement && f.type === "checkbox" ? f.checked : f.value } });
  };
  const when = (t: number, time = true) =>
    (time ? fmtDateTime(new Date(t * 1000)) : new Date(t * 1000).toLocaleString("en-US", { month: "short", day: "numeric" }));
  // `label` is the noun phrase; `hint` is the rest of the sentence, as a tooltip.
  const RULES: { k: string; label: string; hint?: string; num?: [string, string, string, number] }[] = [
    { k: "card_due", label: "Card payment due", hint: "A card payment is coming up", num: ["card_due_days", "", " days ahead", 1] },
    { k: "low_balance", label: "Low forecast balance", hint: "The forecast gets low in the next 30 days", num: ["low_balance_below", "below $", "", 50] },
    { k: "missed", label: "Missed recurring payment", hint: "A recurring payment didn't show up" },
    { k: "big_charge", label: "Large charge", hint: "A large charge posts", num: ["big_charge_over", "over $", "", 50] },
    { k: "review", label: "Transactions to review", hint: "Transactions are waiting for a category (at most once a day)" },
    { k: "sync_failed", label: "Failing bank sync", hint: "Syncing with your bank has been failing for a day" },
    { k: "churn_fee", label: "Annual fee due", hint: "A churning card's annual fee is due within 30 days (unless you're keeping it or have a plan for it)" },
    { k: "churn_bonus", label: "Sign-up bonus deadline", hint: "A card or bank bonus deadline is within 14 days, with requirements left" },
    { k: "churn_plan", label: "Planned card change", hint: "It's time to downgrade, close or change a card, as you planned" },
    { k: "churn_benefit", label: "Card credit resetting", hint: "A card credit with money left is about to reset" },
    { k: "churn_apply", label: "Planned bonus ready", hint: "A card or bank bonus you planned has nothing in the way now, or its offer ends within 14 days" },
  ];
  const b = "font-medium text-foreground";
</script>

{#await data}
  <Card.Root><Card.Content><p class="py-4 text-center text-sm text-muted-foreground">Loading…</p></Card.Content></Card.Root>
{:then l}
  {@const d = l.d}
  {@const sub = l.sub}
  {@const here = sub && d.devices.find((x) => x.endpoint === sub.endpoint)}
  {@const mine = here && !here.unclaimed ? here : null}
  <Card.Root>
    <Card.Header><Card.Title>This device</Card.Title></Card.Header>
    <Card.Content class="flex flex-col gap-3 text-sm leading-relaxed">
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
    </Card.Content>
  </Card.Root>

  <Card.Root>
    <Card.Header><Card.Title>What to tell you about</Card.Title></Card.Header>
    <Card.Content class="flex flex-col">
      {#each RULES as r (r.k)}
        <div class="flex flex-wrap items-center gap-x-4 gap-y-2 border-t py-2.5 first:border-t-0">
          <label class={checkCls} title={r.hint}><input type="checkbox" checked={!!d.prefs[r.k]} use:autosave={savePref(r.k)} /> {r.label}</label>
          {#if r.num}
            {@const [k, pre, post, step] = r.num}
            <!-- Fixed-width prefix and suffix so the inputs line up in one column down the list. -->
            <label class="flex w-full items-center gap-1 pl-6 text-sm text-muted-foreground sm:ml-auto sm:w-auto sm:pl-0"><span class="whitespace-nowrap sm:w-14 sm:text-right">{pre}</span><input class={`${inputCls} h-8 w-24`} type="number" min="0" {step}
              value={d.prefs[k]} use:autosave={savePref(k)} aria-label={`${r.label}: ${pre}…${post}`.replace(": …", ": ")} /><span class="sm:w-20">{post}</span></label>
          {/if}
        </div>
      {/each}
    </Card.Content>
  </Card.Root>

  {#if d.devices.length}
  <Card.Root>
    <Card.Header><Card.Title>Devices</Card.Title></Card.Header>
    <Card.Content>
        <div class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead><tr class="text-left text-xs text-muted-foreground">
              <th class="pb-2 font-medium">Device</th><th class="pb-2 font-medium">Added</th><th class="pb-2 font-medium">Last delivered</th><th class="pb-2"><span class="sr-only">Actions</span></th>
            </tr></thead>
            <tbody>
              {#each d.devices as x (x.endpoint)}
                <tr class="border-t align-top">
                  <td class="py-2 pr-3">{x.device || "Device"}{#if sub && x.endpoint === sub.endpoint}{" "}<Badge variant="secondary">this one</Badge>{/if}
                    {#if x.unclaimed}{" "}<Badge variant="outline" title="Turned on before there was sign-in: it gets nothing until it's turned on again from the device">from before sign-in</Badge>{/if}
                    {#if x.last_error}<div class="text-xs text-destructive">{x.last_error}</div>{/if}</td>
                  <td class="py-2 pr-3 whitespace-nowrap text-muted-foreground">{x.created ? when(x.created, false) : ""}</td>
                  <td class="py-2 pr-3 whitespace-nowrap text-muted-foreground">{x.last_ok ? when(x.last_ok) : "—"}</td>
                  <td class="py-1 text-right"><ConfirmButton confirm="Turn off?" title="Stop notifications on this device" onconfirm={() => removeDevice(x.endpoint, sub)}>Turn off</ConfirmButton></td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
    </Card.Content>
  </Card.Root>
  {/if}

  {#if d.recent.length}
    <Card.Root>
      <Card.Header><Card.Title>Recently sent</Card.Title></Card.Header>
      <Card.Content>
        <table class="w-full text-sm"><tbody>
          {#each d.recent as r, i (i)}
            <tr class="border-t first:border-t-0"><td class="py-2 pr-3">{r.title}</td><td class="py-2 text-right whitespace-nowrap text-muted-foreground">{when(r.sent)}</td></tr>
          {/each}
        </tbody></table>
      </Card.Content>
    </Card.Root>
  {/if}
{:catch err}
  <Card.Root><Card.Content><p class="text-sm">Something went wrong: {err.message}</p>
    <Button class="mt-3" variant="outline" onclick={again}>Try again</Button></Card.Content></Card.Root>
{/await}
