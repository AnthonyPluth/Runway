// @vitest-environment jsdom
// Stale while revalidate on the Transactions page (lib/swr.ts): the second visit paints the last list at once, the
// fresh one swaps in where it is, a failed refresh takes the old rows down, and locking or a change forgets them.
import { render, screen } from "@testing-library/svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), forgetReplies: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn(), dismiss: vi.fn() }) }));

import { lock, lockNow } from "$lib/lock.svelte";
import { clearCache } from "$lib/swr";
import { tx } from "../test/fixtures";
import { resetTxPage, rows, serve, summary } from "../test/txPage";
import Transactions from "./Transactions.svelte";

/** A reply that waits for the test to say when. */
function held<T>() {
  let go!: (v: T) => void, fail!: (e: Error) => void;
  const promise = new Promise<T>((res, rej) => { go = res; fail = rej; });
  return { promise, go, fail };
}
const isList = (path: string) => path.startsWith("/api/transactions?") && !path.includes("ignored=only");

beforeEach(() => { vi.useFakeTimers({ shouldAdvanceTime: true }); clearCache(); resetTxPage(); });
afterEach(() => vi.useRealTimers());

/** Visit once so the page has been seen, then leave. */
async function firstVisit() {
  serve(rows(), 2);
  const first = render(Transactions);
  await screen.findByText("Alpha");
  first.unmount();
}

describe("Transactions page, seen before", () => {
  it("paints the last list before the refresh answers, then swaps in the fresh rows without redrawing", async () => {
    await firstVisit();
    const refresh = held<unknown>();
    serve(rows(), 2, (path) => (isList(path) ? refresh.promise : undefined));
    render(Transactions);
    // Nothing has answered, and the rows are there (dimmed summary: not known to be current)
    expect(await screen.findByText("Alpha")).toBeInTheDocument();
    expect(screen.queryByRole("status", { name: /Loading/ })).not.toBeInTheDocument();
    expect(summary()).toHaveClass("opacity-50");
    // the remembered rows can't be used yet: inert, dimmed and busy
    const rowsBox = screen.getByTestId("tx-rows");
    expect(rowsBox).toHaveProperty("inert", true);
    expect(rowsBox).toHaveAttribute("aria-busy", "true");
    expect(rowsBox).toHaveClass("opacity-60");
    const alpha = screen.getByText("Alpha");
    refresh.go({ items: [tx({ id: "a", payee: "Alpha", category: "Coffee" }), tx({ id: "c", payee: "Charlie", category: "Groceries" })], total: 2, sum: 5 });
    expect(await screen.findByText("Charlie")).toBeInTheDocument();
    expect(screen.queryByText("Bravo")).not.toBeInTheDocument();
    expect(summary()).not.toHaveClass("opacity-50");
    expect(screen.getByTestId("tx-rows")).toHaveProperty("inert", false);
    expect(screen.getByTestId("tx-rows")).toHaveAttribute("aria-busy", "false");
    expect(screen.getByText("Alpha")).toBe(alpha);   // the same row, updated where it is: no redraw, so no lost place
  });

  it("takes the painted rows down when the refresh fails, instead of leaving them looking current", async () => {
    await firstVisit();
    const refresh = held<unknown>();
    serve(rows(), 2, (path) => (isList(path) ? refresh.promise : undefined));
    render(Transactions);
    expect(await screen.findByText("Alpha")).toBeInTheDocument();
    refresh.fail(new Error("Can’t reach Runway."));
    expect(await screen.findByText(/Something went wrong: Can’t reach Runway\./)).toBeInTheDocument();
    expect(screen.queryByText("Alpha")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("shows the loading skeleton, not old rows, after the app locked", async () => {
    await firstVisit();
    lock.phase = "unlocked";
    lockNow(false);   // the server said so (423), or Lock now
    serve(rows(), 2, (path) => (isList(path) ? new Promise(() => {}) : undefined));
    render(Transactions);
    expect(await screen.findByRole("status")).toBeInTheDocument();
    expect(screen.queryByText("Alpha")).not.toBeInTheDocument();
  });
});
