<script lang="ts">
  import { api } from "$lib/api";
  import { app, refreshState } from "$lib/app.svelte";
  import { autosave } from "$lib/autosave";
  import { Button } from "$lib/components/ui/button";
  import { toast } from "svelte-sonner";
  import AiLogTable from "$lib/components/transactions/AiLogTable.svelte";
  import ProblemNote from "./ProblemNote.svelte";
  import SecretInput from "./SecretInput.svelte";
  import ServiceRow from "./ServiceRow.svelte";
  import { checkCls, fieldCls, helpCls, inputCls, linkCls, rowCls, selectCls } from "./ui";
  import { act, errMsg } from "$lib/act";

  // AI categorization through OpenRouter (a key) or your own Ollama server (its address), whichever the provider menu says: the models, and whether new merchants get categorized during
  // syncs. What's sent is said here, since it's your data leaving Runway (categorize.ask_model builds it). The model
  // fields are empty while they're the defaults (their placeholders), and emptying one goes back to its default.
  const st = $derived(app.state!);
  const ollama = $derived(st.ai_provider === "ollama");
  type Field = HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement;

  async function saveKey(f: Field) {
    const v = f.value.trim();
    if (!v) return;
    await api("/api/settings", { method: "POST", body: { openrouter_api_key: v } });
    f.value = "";
    toast.success("Key saved"); await refreshState();
  }
  const shown = (model?: string, fallback?: string) => (model && model !== fallback ? model : "");
  async function saveOllama(f: Field) {
    await api("/api/settings", { method: "POST", body: { ollama_url: f.value.trim() } }); await refreshState();
  }
  async function saveOllamaModel(f: Field) {
    await api("/api/settings", { method: "POST", body: { ollama_model: f.value.trim() } }); await refreshState();
  }
  // The provider is a choice, not a fallback: each keeps its own settings, and only the chosen one is shown and asked.
  async function setProvider(e: Event) {
    const select = e.currentTarget as HTMLSelectElement;
    await act(async () => { await api("/api/settings", { method: "POST", body: { ai_provider: select.value } }); await refreshState(); });
  }
  async function saveModel(key: "llm_model" | "card_ai_model", f: Field) {
    await api("/api/settings", { method: "POST", body: { [key]: f.value.trim() || null } }); await refreshState();
  }
  async function clearKey() {
    await act(async () => { await api("/api/settings", { method: "POST", body: { openrouter_api_key: "" } }); await refreshState(); });
  }
  // A switch that didn't save goes back to how it was.
  async function setFlag(key: "auto_ai_on_sync" | "churn_ai_web", e: Event) {
    const box = e.currentTarget as HTMLInputElement;
    const on = box.checked;
    try {
      await api("/api/settings", { method: "POST", body: { [key]: on } });
      toast.success("Saved"); refreshState();
    } catch (err) { box.checked = !on; toast.error(errMsg(err)); }
  }
</script>

<ServiceRow name="AI categorization" purpose={`${ollama ? "Ollama" : "OpenRouter"} · sorts new transactions into categories`} on={!!st.ai_configured}
  warn={!!st.ai_configured && !!st.last_llm_error}>
  <p class={helpCls}>
    {#if ollama}
      Ask a model on your own <a class={linkCls} href="https://ollama.com" target="_blank" rel="noopener">Ollama</a> server. The requests go only to that server (unless you’ve turned on Sentry, whose AI tracing records prompts),
      and its model has to be pulled there first (<code>ollama pull</code>). Ollama can’t search the web, so card lookups answer from the model’s memory.
    {:else}
      Get an <a class={linkCls} href="https://openrouter.ai/keys" target="_blank" rel="noopener">OpenRouter key</a>.
    {/if}
    The model gets each new merchant’s name and bank description, with one recent amount, date and account type; never account names or numbers.</p>
  <div class={rowCls}>
    <label class={`${fieldCls} w-full sm:w-40`}>Provider
      <select class={`${selectCls} w-full`} value={ollama ? "ollama" : "openrouter"} onchange={setProvider}>
        <option value="openrouter">OpenRouter</option><option value="ollama">Ollama</option></select></label>
    {#if ollama}
      <label class={`${fieldCls} w-full sm:w-64`}>Ollama address
        <input class={inputCls} value={st.ollama_url ?? ""} placeholder="http://localhost:11434" spellcheck="false" autocapitalize="off"
          use:autosave={(f) => saveOllama(f)} /></label>
      <label class={`${fieldCls} w-full sm:w-60`}>Model
        <input class={inputCls} value={shown(st.ollama_model, st.ollama_model_default)} placeholder={st.ollama_model_default} spellcheck="false"
          autocapitalize="off" use:autosave={saveOllamaModel} /></label>
    {:else}
      <SecretInput label="OpenRouter API key" class="w-full sm:w-64" placeholder={st.has_api_key ? "Key saved" : "sk-or-…"} save={saveKey} />
      <label class={`${fieldCls} w-full sm:w-60`}>Categorization model
        <input class={inputCls} value={shown(st.llm_model, st.llm_model_default)} placeholder={st.llm_model_default} spellcheck="false"
          autocapitalize="off" use:autosave={(f) => saveModel("llm_model", f)} /></label>
      <label class={`${fieldCls} w-full sm:w-60`}>Card lookup model
        <input class={inputCls} value={shown(st.card_ai_model, st.card_ai_model_default)} placeholder={st.card_ai_model_default} spellcheck="false"
          autocapitalize="off" use:autosave={(f) => saveModel("card_ai_model", f)} /></label>
      {#if st.has_api_key}<Button variant="outline" onclick={clearKey}>Remove key</Button>{/if}
    {/if}
  </div>
  <label class={checkCls}><input type="checkbox" checked={!!st.auto_ai_on_sync} onchange={(e) => setFlag("auto_ai_on_sync", e)} />
    Categorize new merchants during each sync when the AI is confident</label>
  {#if !ollama}
    <label class={checkCls}><input type="checkbox" checked={st.churn_ai_web !== false} onchange={(e) => setFlag("churn_ai_web", e)} />
      Search the web when filling in a card with AI (Churning)</label>
  {/if}
  {#if st.last_llm_error}<ProblemNote text="The last AI request failed." detail={st.last_llm_error} />{/if}
  {#if st.ai_configured}<AiLogTable />{/if}
</ServiceRow>
