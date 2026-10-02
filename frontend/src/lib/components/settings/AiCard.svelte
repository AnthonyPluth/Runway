<script lang="ts">
  import { api } from "$lib/api";
  import { app, refreshState, reload } from "$lib/app.svelte";
  import { autosave } from "$lib/autosave";
  import * as Alert from "$lib/components/ui/alert";
  import { Button } from "$lib/components/ui/button";
  import { toast } from "svelte-sonner";
  import AiLogTable from "$lib/components/transactions/AiLogTable.svelte";
  import ServiceRow from "./ServiceRow.svelte";
  import TriangleAlert from "@lucide/svelte/icons/triangle-alert";
  import { checkCls, fieldCls, inputCls, rowCls } from "./ui";

  // AI categorization through OpenRouter: the key, the two models, and whether new merchants get categorized during syncs.
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
  async function saveCardModel(f: Field) {
    await api("/api/settings", { method: "POST", body: { card_ai_model: f.value.trim() } }); await refreshState();
  }
  async function clearKey() {
    try { await api("/api/settings", { method: "POST", body: { openrouter_api_key: "" } }); await refreshState(); reload(); }
    catch (err) { toast.error((err as Error).message); }
  }
  async function setFlag(key: "auto_ai_on_sync" | "churn_ai_web", e: Event) {
    try {
      await api("/api/settings", { method: "POST", body: { [key]: (e.currentTarget as HTMLInputElement).checked } });
      toast.success("Saved"); refreshState();
    } catch (err) { toast.error((err as Error).message); }
  }
</script>

<ServiceRow name="AI categorization" purpose="OpenRouter · sorts new transactions into categories" on={!!st.has_api_key}>
  <div class={rowCls}>
    <label class={`${fieldCls} w-full sm:w-64`}>OpenRouter API key
      <input class={inputCls} type="password" placeholder={st.has_api_key ? "•••••••• saved" : "sk-or-…"} autocomplete="off" use:autosave={saveKey} /></label>
    <label class={`${fieldCls} w-full sm:w-60`}>Categorization model
      <input class={inputCls} value={st.llm_model ?? ""} spellcheck="false" use:autosave={saveModel} /></label>
    <label class={`${fieldCls} w-full sm:w-60`}>Card lookup model
      <input class={inputCls} value={st.card_ai_model ?? ""} spellcheck="false" use:autosave={saveCardModel} /></label>
    {#if st.has_api_key}<Button variant="outline" onclick={clearKey}>Remove key</Button>{/if}
  </div>
  <label class={checkCls}><input type="checkbox" checked={!!st.auto_ai_on_sync} onchange={(e) => setFlag("auto_ai_on_sync", e)} />
    Categorize new merchants during each sync when the AI is confident</label>
  <label class={checkCls}><input type="checkbox" checked={st.churn_ai_web !== false} onchange={(e) => setFlag("churn_ai_web", e)} />
    Search the web when filling in a card with AI (Churning)</label>
  {#if st.last_llm_error}
    <Alert.Root variant="destructive"><TriangleAlert /><Alert.Description><p>Last AI error: {st.last_llm_error}</p></Alert.Description></Alert.Root>
  {/if}
  {#if st.has_api_key}<AiLogTable />{/if}
</ServiceRow>
