import { screen, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";

export async function pickCategory(trigger: HTMLElement, value: string): Promise<void> {
  await userEvent.click(trigger);
  const list = await screen.findByRole("listbox");
  const option = within(list).getAllByRole("option").find((o) => o.dataset.value === value);
  if (!option) throw new Error(`No option ${value} in the category picker`);
  await userEvent.click(option);
}

export const pickedValue = (trigger: HTMLElement): string | undefined => trigger.dataset.value;
