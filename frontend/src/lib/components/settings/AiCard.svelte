<script lang="ts">
  import { api } from "$lib/api";
  import { app, refreshState, reload } from "$lib/app.svelte";
  import { autosave } from "$lib/autosave";
  import * as Alert from "$lib/components/ui/alert";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { toast } from "svelte-sonner";
  import TriangleAlert from "@lucide/svelte/icons/triangle-alert";
  import { checkCls, fieldCls, helpCls, inputCls, rowCls, titleNote } from "./ui";

  // AI categorization through OpenRouter: the key, the model, and whether new merchants get categorized during syncs.
  const st = $derived(app.state!);
  type Field = HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement;

  async function saveKey(f: Field) {
    const v = f.value.trim();
    if (!v) return;
    await api("/api/settings", { method: "POST", body: { openrouter_api_key: v } });
    toast.success("API key saved"); await refreshState(); reload();
  }
  async function saveModel(f: Field) {
    await api("/api/settings", { method: "POST", body: { llm_model: f.value.trim() } }); await refreshState();
  }
  async function clearKey() {
    try { await api("/api/settings", { method: "POST", body: { openrouter_api_key: "" } }); await refreshState(); reload(); }
    catch (err) { toast.error((err as Error).message); }
  }
  async function setAuto(e: Event) {
    try {
      await api("/api/settings", { method: "POST", body: { auto_ai_on_sync: (e.currentTarget as HTMLInputElement).checked } });
      toast.success("Saved"); refreshState();
    } catch (err) { toast.error((err as Error).message); }
  }
</script>

<Card.Root>
  <Card.Header><Card.Title>AI categorization <span class={titleNote}>optional, via OpenRouter</span></Card.Title></Card.Header>
  <Card.Content class="flex flex-col gap-3">
    <p class={helpCls}>Only the date, amount, merchant and account type of each transaction are sent.</p>
    <div class={rowCls}>
      <label class={`${fieldCls} w-full sm:w-64`}>OpenRouter API key
        <input class={inputCls} type="password" placeholder={st.has_api_key ? "•••••••• saved" : "sk-or-…"} autocomplete="off" use:autosave={saveKey} /></label>
      <label class={`${fieldCls} w-full sm:w-60`}>Model
        <input class={inputCls} value={st.llm_model ?? ""} spellcheck="false" use:autosave={saveModel} /></label>
      {#if st.has_api_key}<Button variant="outline" onclick={clearKey}>Remove key</Button>{/if}
    </div>
    <label class={checkCls}><input type="checkbox" checked={!!st.auto_ai_on_sync} onchange={setAuto} />
      Categorize new merchants during each sync when the AI is confident</label>
    {#if st.last_llm_error}
      <Alert.Root variant="destructive"><TriangleAlert /><Alert.Description><p>Last AI error: {st.last_llm_error}</p></Alert.Description></Alert.Root>
    {/if}
  </Card.Content>
</Card.Root>
