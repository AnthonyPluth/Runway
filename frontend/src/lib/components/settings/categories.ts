import { api } from "$lib/api";
import { refreshState, reload } from "$lib/app.svelte";
import { loadCategories } from "$lib/categories.svelte";
import { toast } from "svelte-sonner";
import { act } from "$lib/act";

/** The categories changed on the server: load them again where they are (the rows are keyed by name, so they update in
 * place instead of the whole tab redrawing). If they can't be loaded, redraw the page to show the failure. */
export async function categoriesChanged(): Promise<void> {
  try { await refreshState(); await loadCategories(); } catch { reload(); }
}

/** Add a category (the Add form on Settings → Categories, and "+ Sub" on a row). Whether it was added, so the form can close. */
export async function addCategory(name: string, parent: string | null, isTransfer: boolean, isIncome: boolean): Promise<boolean> {
  return act(async () => {
    await api("/api/categories", { method: "POST", body: { name, parent, is_transfer: isTransfer, is_income: isIncome } });
    toast.success(parent ? `Added ${name} under ${parent}` : `Added ${name}`);
    await categoriesChanged();
  });
}
