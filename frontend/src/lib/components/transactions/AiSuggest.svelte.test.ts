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
import { endBatch } from "$lib/undoBatch";
import AiSuggest from "./AiSuggest.svelte";
import type { AiGroup } from "./types";

const group = (count: number, extra: Partial<AiGroup> = {}): AiGroup => ({
  merchant: "Trader Joe's", direction: "out", count, total: -count * 10, examples: [], tx_ids: Array.from({ length: count }, (_, i) => `t${i}`),
  category: "Groceries", new_category: null, confidence: 0.9, ...extra,
});
const was = [{ id: "t0", category: null, category_source: null, confidence: null, needs_review: 1, payee: "TJ", is_split: 0 }];
const applies = () => vi.mocked(api).mock.calls.filter((c) => c[0] === "/api/ai/apply");

async function setup(g: AiGroup | AiGroup[]) {
  const gs = Array.isArray(g) ? g : [g];
  vi.mocked(api).mockImplementation((async (path: string, opts?: { body?: { tx_ids?: string[] } }) => path === "/api/ai/suggest" ? gs
    : path === "/api/ai/apply" ? { updated: opts?.body?.tx_ids?.length ?? 0, category: "Groceries", created: false, offer_rule: null, was } : {}) as never);
  const onchanged = vi.fn();
  const r = render(AiSuggest, { onasked: vi.fn(), onchanged });
  await (r.component as unknown as { run: () => Promise<void> }).run();
  return { onchanged, run: (withSkipped?: boolean) => (r.component as unknown as { run: (w?: boolean) => Promise<void> }).run(withSkipped) };
}
async function setupOne(g: AiGroup) {
  vi.mocked(api).mockImplementation((async (path: string) => path === "/api/ai/suggest" ? [g]
    : path === "/api/ai/apply" ? { updated: g.count, category: "Groceries", created: false, offer_rule: null, was } : {}) as never);
  const onchanged = vi.fn();
  const r = render(AiSuggest, { onasked: vi.fn(), onchanged });
  await (r.component as unknown as { run: () => Promise<void> }).run();
  return onchanged;
}

beforeEach(() => {
  endBatch(); localStorage.clear();
  vi.mocked(api).mockReset();
  vi.mocked(toast).mockClear(); vi.mocked(toast.error).mockClear();
  categories.list = [category("Groceries")];
});

describe("AI suggestions → Apply", () => {
  it("applies a few straight away and offers an undo with what they were", async () => {
    const onchanged = await setupOne(group(3));
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
    await setupOne(group(10));
    await userEvent.click(await screen.findByRole("button", { name: "Apply to 10" }));
    const dialog = await screen.findByRole("dialog", { name: "Apply Groceries to 10 transactions?" });
    expect(applies()).toHaveLength(0);
    await userEvent.click(within(dialog).getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(applies()).toHaveLength(1));
    await waitFor(() => expect(screen.queryByText("Trader Joe's")).not.toBeInTheDocument());
  });

  it("applies nothing when you cancel, and keeps the line", async () => {
    await setupOne(group(25));
    await userEvent.click(await screen.findByRole("button", { name: "Apply to 25" }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Cancel" }));
    expect(applies()).toHaveLength(0);
    expect(screen.getByRole("button", { name: "Apply to 25" })).toBeEnabled();
  });

  it("says a new category will be added", async () => {
    await setupOne(group(12, { category: null, new_category: { name: "Pets" } }));
    await userEvent.click(await screen.findByRole("button", { name: "Apply to 12" }));
    expect(await screen.findByRole("dialog", { name: "Apply Pets to 12 transactions?" })).toHaveTextContent("This adds the category");
  });
});

describe("AI suggestions → the card", () => {
  const many = (n: number) => Array.from({ length: n }, (_, i) => group(n - i, { merchant: `Shop ${i + 1}`, tx_ids: [`s${i}`] }));

  it("shows the five biggest merchants, and the rest on request", async () => {
    await setup(many(8));
    expect(await screen.findByText("Shop 5")).toBeInTheDocument();
    expect(screen.queryByText("Shop 6")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Show 3 more" }));
    expect(screen.getByText("Shop 8")).toBeInTheDocument();
  });

  it("says how sure the AI is as High, Medium or Low, the percent in the tooltip", async () => {
    await setup([group(3, { merchant: "A", confidence: 0.95 }), group(2, { merchant: "B", confidence: 0.75 }), group(1, { merchant: "C", confidence: 0.4 })]);
    expect(await screen.findByText("High")).toHaveAttribute("title", "95% sure");
    expect(screen.getByText("Medium")).toHaveAttribute("title", "75% sure");
    expect(screen.getByText("Low")).toHaveAttribute("title", "40% sure");
  });

  it("applies every High one with one click, and only those", async () => {
    await setup([group(3, { merchant: "A", confidence: 0.95, tx_ids: ["a"] }), group(2, { merchant: "B", confidence: 0.92, tx_ids: ["b"] }),
      group(1, { merchant: "C", confidence: 0.5, tx_ids: ["c"] })]);
    await userEvent.click(await screen.findByRole("button", { name: /Apply all High/ }));
    await waitFor(() => expect(applies()).toHaveLength(2));
    expect(applies().map((c) => (c[1] as { body: { tx_ids: string[] } }).body.tx_ids)).toEqual([["a"], ["b"]]);
    expect(screen.getByText("C")).toBeInTheDocument();
    expect(vi.mocked(toast).mock.calls.at(-1)![0]).toBe("2 changed");
  });

  it("asks first when High adds up to ten or more", async () => {
    await setup([group(6, { merchant: "A", confidence: 0.95 }), group(5, { merchant: "B", confidence: 0.95 })]);
    await userEvent.click(await screen.findByRole("button", { name: /Apply all High/ }));
    expect(await screen.findByRole("dialog", { name: "Apply 2 suggestions to 11 transactions?" })).toBeInTheDocument();
    expect(applies()).toHaveLength(0);
  });

  it("doesn't ask about a skipped merchant again, unless asked to include them", async () => {
    const { run } = await setup([group(3, { merchant: "A" }), group(2, { merchant: "B" })]);
    await userEvent.click(within((await screen.findByText("A")).closest(".border-t")! as HTMLElement).getByRole("button", { name: "Skip" }));
    expect(screen.queryByText("A")).not.toBeInTheDocument();
    await run();
    expect(api).toHaveBeenLastCalledWith("/api/ai/suggest", { method: "POST", body: { skip: ["A"] } });
    await run(true);
    expect(api).toHaveBeenLastCalledWith("/api/ai/suggest", { method: "POST", body: { skip: [] } });
  });
});
