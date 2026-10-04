// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

import { api } from "$lib/api";
import { toast } from "svelte-sonner";
import { bodyOf, calls, card } from "./fixtures";
import type { UpcomingItem } from "./types";
import Upcoming from "./Upcoming.svelte";

beforeEach(() => {
  vi.mocked(api).mockReset();
  vi.mocked(api).mockResolvedValue({ ok: true } as never);
  vi.mocked(toast).mockReset();
  vi.mocked(toast.error).mockReset();
});

describe("upcoming", () => {
  const item = (over: Partial<UpcomingItem>): UpcomingItem => ({ date: "2026-10-20", kind: "plan", card_id: 1, owner: "Alex", title: "Downgrade Venture X", detail: "", warn: false, ...over });
  const setup = (items: UpcomingItem[]) => {
    const onchanged = vi.fn();
    render(Upcoming, { items, cards: [], today: "2026-09-30", showOwner: false, onchanged });
    return onchanged;
  };

  it("shows the first 4, and the rest on asking", async () => {
    setup([1, 2, 3, 4, 5, 6].map((n) => item({ title: `Thing ${n}`, card_id: n })));
    expect(screen.queryByText("Thing 5")).not.toBeInTheDocument();
    expect(screen.getByText("Thing 4")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Show all 6" }));
    expect(screen.getByText("Thing 6")).toBeInTheDocument();
  });

  it("labels the new kinds", () => {
    setup([item({}), item({ kind: "benefit", benefit_id: 4, title: "Travel credit: $200 left" }), item({ kind: "apply", card_id: null, title: "You can apply for Gold" }),
      item({ kind: "offer_ends", card_id: null, title: "Offer for Gold ends Oct 25" })]);
    for (const l of ["Your plan", "Card credit", "Apply", "Offer ends"]) expect(screen.getAllByText(l).length).toBeGreaterThan(0);
  });

  it("checks a plan off, tells what changed, and undoes it", async () => {
    vi.mocked(api).mockResolvedValue({ changes: ["Venture X is marked product-changed on 2026-09-30"] } as never);
    const onchanged = setup([item({})]);
    await userEvent.click(screen.getByRole("button", { name: 'Mark "Downgrade Venture X" done' }));
    await waitFor(() => expect(calls("/api/churning/cards/1/plan/done")).toHaveLength(1));
    const [msg, opts] = vi.mocked(toast).mock.calls.at(-1)! as [string, { description: string; action: { label: string; onClick: () => void } }];
    expect(msg).toBe("Done");
    expect(opts.description).toContain("Venture X is marked product-changed");
    expect(onchanged).toHaveBeenCalled();
    opts.action.onClick();
    await waitFor(() => expect(calls("/api/churning/cards/1/plan/undo")).toHaveLength(1));
  });

  it("checks a to-do off, and its toast puts it back", async () => {
    const onchanged = setup([item({ kind: "task", task_id: 9, card_id: 1, title: "Call Citi" })]);
    await userEvent.click(screen.getByRole("button", { name: 'Mark "Call Citi" done' }));
    await waitFor(() => expect(bodyOf(calls("/api/churning/tasks/9")[0])).toEqual({ done: true }));
    const [msg, opts] = vi.mocked(toast).mock.calls.at(-1)! as [string, { description: string; action: { label: string; onClick: () => void } }];
    expect(msg).toBe("Done");
    expect(opts.description).toBe("Call Citi");
    expect(opts.action.label).toBe("Undo");
    expect(onchanged).toHaveBeenCalled();
    opts.action.onClick();
    await waitFor(() => expect(bodyOf(calls("/api/churning/tasks/9")[1])).toEqual({ done: false }));
  });

  it("says why a to-do couldn't be checked off, and offers no undo", async () => {
    vi.mocked(api).mockRejectedValue(new Error("To-do not found"));
    const onchanged = setup([item({ kind: "task", task_id: 9, card_id: 1, title: "Call Citi" })]);
    await userEvent.click(screen.getByRole("button", { name: 'Mark "Call Citi" done' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("To-do not found"));
    expect(toast).not.toHaveBeenCalled();
    expect(onchanged).not.toHaveBeenCalled();
  });

  it("colors a deadline's date red within 7 days or late, amber within 30, and muted after", () => {
    setup([
      item({ title: "Late", date: "2026-09-27", card_id: 1 }), item({ title: "Soon", date: "2026-10-07", card_id: 2 }),
      item({ title: "Month", date: "2026-10-30", card_id: 3 }), item({ title: "Later", date: "2026-12-20", card_id: 4 }),
    ]);
    const date = (title: string) => screen.getByText(title).closest("li")!.firstElementChild as HTMLElement;
    expect(date("Late")).toHaveTextContent("3 days overdue");
    expect(date("Late")).toHaveClass("text-loss");
    expect(date("Soon")).toHaveClass("text-loss");
    expect(date("Month")).toHaveClass("text-warning");
    expect(date("Later")).toHaveClass("text-muted-foreground");
    expect(date("Soon")).toHaveAttribute("title", "in 7 days");
  });

  it("doesn't color a chance to apply, which is good news and not a deadline", () => {
    setup([item({ kind: "apply", card_id: null, title: "You can apply for Gold", date: "2026-10-01" })]);
    expect(screen.getByText("You can apply for Gold").closest("li")!.firstElementChild).toHaveClass("text-muted-foreground");
  });

  it("marks a credit used from Upcoming", async () => {
    vi.mocked(api).mockResolvedValue({ id: 2 } as never);
    setup([item({ kind: "benefit", benefit_id: 4, title: "Travel credit: $200 left" })]);
    await userEvent.click(screen.getByRole("button", { name: 'Mark "Travel credit: $200 left" used' }));
    await waitFor(() => expect(calls("/api/churning/benefits/4/use")).toHaveLength(1));
  });

  it("snoozes a to-do", async () => {
    vi.mocked(api).mockResolvedValue({ snooze_until: "2026-10-07" } as never);
    setup([item({ kind: "task", task_id: 9, card_id: 1, title: "Call Citi" })]);
    await userEvent.click(screen.getByRole("button", { name: 'Snooze "Call Citi"' }));
    await userEvent.click(screen.getByRole("button", { name: "1 week" }));
    await waitFor(() => expect(bodyOf(calls("/api/churning/tasks/9/snooze")[0])).toEqual({ days: 7 }));
  });
});

describe("empty sections", () => {
  it("Upcoming is one line with an inline Add a to-do that opens the form", async () => {
    render(Upcoming, { items: [], cards: [card()], today: "2026-09-30", showOwner: false, onchanged: vi.fn() });
    expect(screen.getByText("nothing in the next six months")).toBeInTheDocument();
    expect(document.querySelector("[data-slot=card-title]")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Add a to-do" }));
    expect(screen.getByLabelText("What to do")).toBeInTheDocument();
  });

  it("Upcoming offers no to-do action when there is no open card to attach it to", () => {
    render(Upcoming, { items: [], cards: [], today: "2026-09-30", showOwner: false, onchanged: vi.fn() });
    expect(screen.queryByRole("button", { name: "Add a to-do" })).toBeNull();
  });
});
