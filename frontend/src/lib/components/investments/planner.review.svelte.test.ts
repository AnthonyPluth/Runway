// @vitest-environment jsdom
import { cleanup, screen, waitFor } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));

import { api } from "$lib/api";
import { data, resetPlanner, setup } from "../../../test/planner";

// A plan that's still Runway's own figures is reviewed, not shown: no chance, verdict or runs-out age until the
// household confirms it, and nothing is saved (so it stays the default) until then.
beforeEach(resetPlanner);
afterEach(() => { cleanup(); vi.useRealTimers(); });

type Body = { body: { plan: { people: { name: string }[] } } };
const user = () => userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
const show = () => screen.getByRole("button", { name: /Show my projection|Retry/ });
const noResults = () => {
  expect(screen.queryByText("Chance your money lasts")).not.toBeInTheDocument();
  expect(screen.queryByTestId("verdict")).not.toBeInTheDocument();
  expect(screen.queryByText("Runs out")).not.toBeInTheDocument();
  expect(screen.queryByText(/^\d+%$/)).not.toBeInTheDocument();
  expect(screen.queryByRole("img", { name: /Projected/ })).not.toBeInTheDocument();
};

describe("reviewing a default plan", () => {
  it("leads with the assumptions to review and holds back the verdict", () => {
    setup(data({ is_default: true }));
    expect(screen.getByRole("heading", { name: "Review your assumptions" })).toBeInTheDocument();
    for (const name of [/^Name/, /Retires at/, /Saves a year/, /Born in/, /Plan until age/, /Return while saving/, /Return in retirement/, /Ups and downs/, /Inflation/]) {
      expect(screen.getByLabelText(name)).toBeInTheDocument();
    }
    expect(screen.getByRole("textbox", { name: "Yearly spending in retirement" })).toBeInTheDocument();
    expect(screen.getByTestId("starting-from")).toHaveTextContent("Starting from $400,000 invested today");
    expect(show()).toBeInTheDocument();
    noResults();
  });

  it("holds back a verdict even when the sample plan runs out", () => {
    setup(data({ is_default: true, current: 1000 }, { spending: 200000, people: [{ name: "Ann", birth_year: 1961, retire_age: 65, savings: 0 }] }));
    noResults();
  });

  it("keeps edits as a draft: a name change isn't saved after the debounce, so it stays the default", async () => {
    setup(data({ is_default: true }));
    await user().type(screen.getByLabelText(/^Name/), "x");
    await vi.advanceTimersByTimeAsync(5000);
    expect(api).not.toHaveBeenCalled();
    noResults();
  });

  it("doesn't save the draft as you leave either", async () => {
    const { unmount } = setup(data({ is_default: true }));
    await user().type(screen.getByLabelText(/^Name/), "x");
    unmount();
    await vi.advanceTimersByTimeAsync(5000);
    expect(api).not.toHaveBeenCalled();
  });

  it("saves once on Show my projection, then shows the results for the saved plan", async () => {
    setup(data({ is_default: true }));
    const u = user();
    await u.type(screen.getByLabelText(/^Name/), "x");
    await u.click(show());
    await waitFor(() => expect(screen.getByText("Chance your money lasts")).toBeInTheDocument());
    expect(api).toHaveBeenCalledTimes(1);
    const [path, opts] = vi.mocked(api).mock.calls[0] as unknown as [string, { method: string } & Body];
    expect(path).toBe("/api/investments/plan");
    expect(opts.method).toBe("POST");
    expect(opts.body.plan.people[0].name).toBe("Annx");
    expect(screen.queryByTestId("starting-estimate")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Start over/ })).toBeInTheDocument();
  });

  it("keeps the draft with the reason and a Retry when the save fails, and shows nothing until it works", async () => {
    vi.mocked(api).mockRejectedValueOnce(new Error("Locked"));
    setup(data({ is_default: true }));
    const u = user();
    await u.type(screen.getByLabelText(/^Name/), "x");
    await u.click(show());
    expect(await screen.findByRole("alert")).toHaveTextContent("Not saved: Locked");
    noResults();
    expect(screen.getByLabelText(/^Name/)).toHaveValue("Annx");
    await u.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.getByText("Chance your money lasts")).toBeInTheDocument());
    expect(api).toHaveBeenCalledTimes(2);
  });

  it("says which age is wrong and doesn't save or show anything", async () => {
    setup(data({ is_default: true }));
    const u = user();
    const retires = screen.getByRole("spinbutton", { name: /Retires at/ });
    await u.clear(retires);
    await u.type(retires, "30");
    await u.click(show());
    expect(screen.getAllByText("At least 40, your age now").length).toBeGreaterThan(0);
    expect(api).not.toHaveBeenCalled();
    noResults();
  });

  it("asks for a birth year, a retirement age and an age to plan to when they're missing", async () => {
    setup(data({ is_default: true }));
    const u = user();
    await u.clear(screen.getByLabelText("Born in"));
    await u.clear(screen.getByRole("spinbutton", { name: /Retires at/ }));
    await u.clear(screen.getByRole("spinbutton", { name: /Plan until age/ }));
    await u.click(show());
    expect(screen.getByText(/Enter the year born/)).toBeInTheDocument();
    expect(screen.getByText("Enter a retirement age")).toBeInTheDocument();
    expect(screen.getByText("Enter an age to plan until")).toBeInTheDocument();
    expect(api).not.toHaveBeenCalled();
  });

  it("saves an edit made while the confirmation is on its way", async () => {
    let done: (v: unknown) => void = () => {};
    vi.mocked(api).mockImplementationOnce(() => new Promise((r) => { done = r; }));
    setup(data({ is_default: true }));
    const u = user();
    await u.click(show());
    await u.type(screen.getByLabelText(/^Name/), "x");
    done({});
    await waitFor(() => expect(screen.getByText("Chance your money lasts")).toBeInTheDocument());
    await vi.advanceTimersByTimeAsync(1000);
    expect(api).toHaveBeenCalledTimes(2);
    expect((vi.mocked(api).mock.calls[1][1] as unknown as Body).body.plan.people[0].name).toBe("Annx");
  });

  it("goes back to the review after Start over, still without saving edits", async () => {
    setup();
    const u = user();
    await u.click(screen.getByRole("button", { name: /Start over/ }));
    await u.click(screen.getByRole("button", { name: /Start over\? This clears/ }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "Review your assumptions" })).toBeInTheDocument());
    noResults();
    vi.mocked(api).mockClear();
    await u.type(screen.getByLabelText(/^Name/), "x");
    await vi.advanceTimersByTimeAsync(5000);
    expect(api).not.toHaveBeenCalled();
  });
});

describe("a plan you've saved", () => {
  it("shows its results at once and keeps autosaving, after the debounce and as you leave", async () => {
    const { unmount } = setup(data());
    expect(screen.queryByRole("heading", { name: "Review your assumptions" })).not.toBeInTheDocument();
    expect(screen.getByText("Chance your money lasts")).toBeInTheDocument();
    await user().type(screen.getByLabelText(/^Name/), "x");
    expect(api).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(800);
    expect(api).toHaveBeenCalledTimes(1);
    await user().type(screen.getByLabelText(/^Name/), "y");
    unmount();
    expect(api).toHaveBeenCalledTimes(2);
  });
});
