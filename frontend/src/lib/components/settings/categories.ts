import { api } from "$lib/api";
import { reload } from "$lib/app.svelte";
import { toast } from "svelte-sonner";
import { act } from "$lib/act";

/** Add a category (the Add form on Settings → Categories, and "+ Sub" on a row). */
export async function addCategory(name: string, parent: string | null, isTransfer: boolean, isIncome: boolean): Promise<void> {
  await act(async () => {
    await api("/api/categories", { method: "POST", body: { name, parent, is_transfer: isTransfer, is_income: isIncome } });
    toast.success(parent ? `Added ${name} under ${parent}` : `Added ${name}`);
    reload();
  });
}
