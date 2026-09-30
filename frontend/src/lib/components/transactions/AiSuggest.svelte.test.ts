// @vitest-environment jsdom
import { render, screen, waitFor, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", async (orig) => ({ ...(await orig<typeof import("$lib/api")>()), api: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn(), dismiss: vi.fn() }) }));

import { api } from "$lib/api";
import { categories } from "$lib/categories.svelte";
import { toast } from "svelte-sonner";
import { category } from "../../../test/fixtures";
import AiSuggest from "./AiSuggest.svelte";
import type { AiGroup } from "./types";

const group = (count: number, extra: Partial<AiGroup> = {}): AiGroup => ({
  merchant: "Trader Joe's", direction: "out", count, total: -count * 10, examples: [], tx_ids: Array.from({ length: count }, (_, i) => `t${i}`),
  category: "Groceries", new_category: null, confidence: 0.9, ...extra,
});
const was = [{ id: "t0", category: null, category_source: null, confidence: null, needs_review: 1, payee: "TJ", is_split: 0 }];
const applies = () => vi.mocked(api).mock.calls.filter((c) => c[0] === "/api/ai/apply");

async function setup(g: AiGroup) {
  vi.mocked(api).mockImplementation((async (path: string) => path === "/api/ai/suggest" ? [g]
    : path === "/api/ai/apply" ? { updated: g.count, category: "Groceries", created: false, offer_rule: null, was } : {}) as never);
  const onchanged = vi.fn();
  const r = render(AiSuggest, { onasked: vi.fn(), onchanged });
  await (r.component as unknown as { run: () => Promise<void> }).run();
  return onchanged;
}

beforeEach(() => {
  vi.mocked(api).mockReset();
  vi.mocked(toast).mockClear(); vi.mocked(toast.error).mockClear();
  categories.list = [category("Groceries")];
});

describe("AI suggestions → Apply", () => {
  it("applies a few straight away and offers an undo with what they were", async () => {
    const onchanged = await setup(group(3));
    await userEvent.click(await screen.findByRole("button", { name: "Apply to 3" }));
    await waitFor(() => expect(applies()).toHaveLength(1));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(toast).toHaveBeenCalledWith("Trader Joe's: Groceries applied to 3", expect.objectContaining({ action: expect.objectContaining({ label: "Undo" }) })));
    expect(onchanged).toHaveBeenCalled();
    const { action } = vi.mocked(toast).mock.calls.at(-1)![1] as unknown as { action: { onClick: () => Promise<void> } };
    await action.onClick();
    expect(vi.mocked(api)).toHaveBeenCalledWith("/api/transactions/bulk", { method: "POST", body: { restore: was } });
    expect(onchanged).toHaveBeenCalledTimes(2);
  });

  it("asks first from ten up, with the category and the count", async () => {
    await setup(group(10));
    await userEvent.click(await screen.findByRole("button", { name: "Apply to 10" }));
    const dialog = await screen.findByRole("dialog", { name: "Apply Groceries to 10 transactions?" });
    expect(applies()).toHaveLength(0);
    await userEvent.click(within(dialog).getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(applies()).toHaveLength(1));
    await waitFor(() => expect(screen.queryByText("Trader Joe's")).not.toBeInTheDocument());   // the line is done
  });

  it("applies nothing when you cancel, and keeps the line", async () => {
    await setup(group(25));
    await userEvent.click(await screen.findByRole("button", { name: "Apply to 25" }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Cancel" }));
    expect(applies()).toHaveLength(0);
    expect(screen.getByRole("button", { name: "Apply to 25" })).toBeEnabled();
  });

  it("says a new category will be added", async () => {
    await setup(group(12, { category: null, new_category: { name: "Pets" } }));
    await userEvent.click(await screen.findByRole("button", { name: "Apply to 12" }));
    expect(await screen.findByRole("dialog", { name: "Apply Pets to 12 transactions?" })).toHaveTextContent("This adds the category");
  });
});
