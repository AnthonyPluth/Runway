// @vitest-environment jsdom
// Stale while revalidate on the Recurring page (lib/swr.ts): the second visit shows the items at once, held still and
// marked as refreshing (they come without due dates), then the fresh ones; a failed refresh says it couldn't load.
import { render, screen } from "@testing-library/svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

import { api } from "$lib/api";
import { app } from "$lib/app.svelte";
import { clearCache } from "$lib/swr";
import Recurring from "./Recurring.svelte";

const item = (extra: object = {}) => ({ id: 7, name: "Rent", account_id: "a1", amount: -900, frequency: "monthly", active: 1, matched_count: 2,
  next_date: "2026-11-01", ...extra });
const account = { id: "a1", name: "Checking", kind: "checking", hidden: 0, balance: 50 };

function held<T>() {
  let go!: (v: T) => void, fail!: (e: Error) => void;
  const promise = new Promise<T>((res, rej) => { go = res; fail = rej; });
  return { promise, go, fail };
}
/** The API: the recurring reply is whatever `recurring` says. */
function serve(recurring: () => unknown) {
  vi.mocked(api).mockImplementation((async (path: string) => {
    if (path === "/api/accounts") return [account];
    if (path === "/api/recurring") return recurring();
    return [];
  }) as never);
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  clearCache();
  vi.mocked(api).mockReset();
  app.state = { connected: true };
});
afterEach(() => vi.useRealTimers());

async function firstVisit() {
  serve(() => [item()]);
  const first = render(Recurring);
  await screen.findByText("Rent");
  first.unmount();
}

describe("Recurring page, seen before", () => {
  it("shows the items at once, held still and refreshing, and the fresh ones replace them", async () => {
    await firstVisit();
    const refresh = held<unknown>();
    serve(() => refresh.promise);
    render(Recurring);
    expect(await screen.findByText("Rent")).toBeInTheDocument();
    expect(screen.getByText("Refreshing…")).toBeInTheDocument();
    expect(screen.getByText("Rent").closest("[aria-busy=true]")).toHaveProperty("inert", true);
    expect(screen.getAllByRole("button", { name: "Add" })[0]).toBeDisabled();
    refresh.go([item({ name: "Gym", amount: -950 })]);
    expect(await screen.findByText("Gym")).toBeInTheDocument();
    expect(screen.queryByText("Refreshing…")).not.toBeInTheDocument();
    expect(screen.getByText("Gym").closest("[aria-busy]")).toHaveProperty("inert", false);
  });

  it("paints no due date from before: the remembered item has none until the server answers", async () => {
    await firstVisit();
    serve(() => new Promise(() => {}));
    render(Recurring);
    await screen.findByText("Rent");
    expect(document.body.textContent).not.toMatch(/Nov(ember)? 1\b|due/i);
  });

  it("says it couldn't load, and shows no old items, when the refresh fails", async () => {
    await firstVisit();
    const refresh = held<unknown>();
    serve(() => refresh.promise);
    render(Recurring);
    await screen.findByText("Rent");
    refresh.fail(new Error("Down"));
    expect(await screen.findByText("Couldn’t load your recurring items.")).toBeInTheDocument();
    expect(screen.queryByText("Rent")).not.toBeInTheDocument();
  });

  it("starts from the loading placeholder once the cache has been emptied", async () => {
    await firstVisit();
    clearCache();
    serve(() => new Promise(() => {}));
    render(Recurring);
    expect(screen.getByRole("status", { name: "Loading recurring items" })).toBeInTheDocument();
    expect(screen.queryByText("Rent")).not.toBeInTheDocument();
  });
});
