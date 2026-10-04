// @vitest-environment jsdom
import { render, screen, waitFor, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({
  toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }),
}));

import { api } from "$lib/api";
import { toast } from "svelte-sonner";
import { bodyOf, calls, churning, wish } from "./fixtures";
import Planned from "./Planned.svelte";

beforeEach(() => {
  vi.mocked(api).mockReset();
  vi.mocked(api).mockResolvedValue({ ok: true } as never);
  vi.mocked(toast).mockReset();
  vi.mocked(toast.error).mockReset();
});

const menu = (name: string) =>
  userEvent.click(
    screen.getByRole("button", { name: `More actions for ${name}` }),
  );

describe("empty sections", () => {
  it("Planned is one line with an inline action that opens the form", async () => {
    render(Planned, {
      d: churning({ wishlist: [] }),
      person: "",
      showOwner: false,
      onchanged: vi.fn(),
      onapplied: vi.fn(),
    });
    expect(document.querySelector("[data-slot=card-title]")).toBeNull();
    await userEvent.click(
      screen.getByRole("button", { name: "Plan a card or bonus" }),
    );
    expect(
      screen.getByText("Planned", { selector: "[data-slot=card-title]" }),
    ).toBeInTheDocument();
  });
});

describe("planned", () => {
  const setup = (wishlist = [wish()], scores = {}) => {
    const onchanged = vi.fn(),
      onapplied = vi.fn();
    render(Planned, {
      d: churning({ wishlist, scores }),
      person: "",
      showOwner: true,
      onchanged,
      onapplied,
    });
    return { onchanged, onapplied };
  };

  it("lists what's in the way of a planned card, and says when nothing is", () => {
    setup([
      wish({
        id: 1,
        product: "Sapphire Preferred",
        blockers: [
          { kind: "five24", text: "Alex is at 5/24", date: "2026-10-21" },
        ],
        earliest_apply: "2026-10-21",
        hints: ["Still spending toward the Gold bonus"],
      }),
      wish({
        id: 2,
        product: "Platinum",
        ready: true,
        status: "ready",
        priority: 2,
      }),
    ]);
    const first = screen.getByRole("list", {
      name: "What's in the way of Sapphire Preferred",
    });
    expect(within(first).getByText(/Alex is at 5\/24/)).toBeInTheDocument();
    expect(screen.getByText(/Earliest you can apply/)).toBeInTheDocument();
    expect(
      screen.getByText("Still spending toward the Gold bonus"),
    ).toBeInTheDocument();
    expect(screen.getAllByText("Ready to apply")).toHaveLength(1);
    expect(
      screen.getByText("Sapphire Preferred").closest("li"),
    ).not.toHaveTextContent("Ready to apply");
  });

  it("shows the score against what a planned card wants", () => {
    setup(
      [
        wish({
          min_score: 740,
          blockers: [
            { kind: "score", text: "Wants a score of 740", date: null },
          ],
        }),
      ],
      {
        Alex: {
          owner: "Alex",
          score: 705,
          as_of: "2026-09-03",
          source: "Credit Karma",
          history: [],
        },
      },
    );
    expect(screen.getByText("705 of 740 wanted")).toBeInTheDocument();
    expect(
      screen.getByText("Alex's credit score").parentElement,
    ).toHaveTextContent("705");
  });

  it("marks a planned card applied and opens the new card", async () => {
    vi.mocked(api).mockResolvedValue({
      kind: "card",
      id: 12,
      wish_id: 1,
    } as never);
    const { onapplied } = setup();
    await userEvent.click(
      screen.getByRole("button", { name: "I applied for Sapphire Preferred" }),
    );
    await waitFor(() =>
      expect(calls("/api/churning/wishlist/1/applied")).toHaveLength(1),
    );
    expect(onapplied).toHaveBeenCalledWith("card", 12);
  });

  it("links to where to apply, in a new tab, only when a link was entered", () => {
    setup([
      wish({ id: 1, apply_url: "https://creditcards.chase.com/apply" }),
      wish({ id: 2, product: "Gold" }),
    ]);
    const link = screen.getByRole("link", {
      name: "Open the application for Sapphire Preferred",
    });
    expect(link).toHaveAttribute("href", "https://creditcards.chase.com/apply");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link.getAttribute("rel")).toContain("noopener");
    expect(
      screen.queryByRole("link", { name: "Open the application for Gold" }),
    ).not.toBeInTheDocument();
  });

  it("collapses applied and dropped items under a toggle", async () => {
    setup([
      wish(),
      wish({ id: 2, product: "Old one", status: "dropped" }),
      wish({
        id: 3,
        product: "Got it",
        status: "applied",
        applied_on: "2026-09-01",
      }),
    ]);
    expect(screen.queryByText("Old one")).not.toBeInTheDocument();
    expect(screen.getByText(/2 applied or dropped ·/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Show" }));
    expect(screen.getByText("Old one")).toBeInTheDocument();
    expect(screen.getByText("Got it")).toBeInTheDocument();
  });

  it("deletes an applied or dropped item only after a dialog asks, and offers it only for those", async () => {
    const { onchanged } = setup([
      wish(),
      wish({ id: 2, product: "Old one", status: "dropped" }),
    ]);
    expect(
      screen.queryByRole("button", { name: /^Delete/ }),
    ).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Show" }));
    await menu("Old one");
    await userEvent.click(
      screen.getByRole("button", { name: "Delete Old one" }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "Delete Old one?",
    });
    expect(calls(/remove/)).toHaveLength(0);
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Delete" }),
    );
    await waitFor(() =>
      expect(calls("/api/churning/wishlist/2/remove")).toHaveLength(1),
    );
    await waitFor(() => expect(onchanged).toHaveBeenCalled());
  });

  it("keeps the dialog's item when Cancel is pressed, and shows a refusal as a toast, leaving the dialog open", async () => {
    setup([wish({ id: 2, product: "Old one", status: "dropped" }), wish()]);
    await userEvent.click(screen.getByRole("button", { name: "Show" }));
    await menu("Old one");
    await userEvent.click(
      screen.getByRole("button", { name: "Delete Old one" }),
    );
    await userEvent.click(
      within(
        await screen.findByRole("dialog", { name: "Delete Old one?" }),
      ).getByRole("button", { name: "Cancel" }),
    );
    expect(calls(/remove/)).toHaveLength(0);
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "Delete Old one?" }),
      ).toBeNull(),
    );
    await waitFor(() =>
      expect(document.body.style.pointerEvents).not.toBe("none"),
    );
    vi.mocked(api).mockRejectedValue(new Error("Planned item not found"));
    await menu("Old one");
    await userEvent.click(
      screen.getByRole("button", { name: "Delete Old one" }),
    );
    await userEvent.click(
      within(
        await screen.findByRole("dialog", { name: "Delete Old one?" }),
      ).getByRole("button", { name: "Delete" }),
    );
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith("Planned item not found"),
    );
    expect(
      screen.getByRole("dialog", { name: "Delete Old one?" }),
    ).toBeInTheDocument();
  });

  it("drops an item from its menu, says so with an Undo, and the Undo wants it back as it was", async () => {
    const { onchanged } = setup([
      wish({ id: 4, status: "ready", ready: true }),
    ]);
    await menu("Sapphire Preferred");
    await userEvent.click(
      screen.getByRole("button", { name: "Drop Sapphire Preferred" }),
    );
    await waitFor(() =>
      expect(bodyOf(calls("/api/churning/wishlist/4")[0])).toEqual({
        status: "dropped",
      }),
    );
    const [msg, opts] = vi.mocked(toast).mock.calls.at(-1)! as [
      string,
      { action: { label: string; onClick: () => void } },
    ];
    expect(msg).toBe("Dropped Sapphire Preferred");
    expect(opts.action.label).toBe("Undo");
    expect(onchanged).toHaveBeenCalled();
    opts.action.onClick();
    await waitFor(() =>
      expect(bodyOf(calls("/api/churning/wishlist/4")[1])).toEqual({
        status: "ready",
      }),
    );
  });

  it("keeps the main actions on the row and the secondary ones in a menu that closes on Escape", async () => {
    setup([wish(), wish({ id: 2, product: "Gold", priority: 2 })]);
    const row = document.querySelector("[data-wish='1']") as HTMLElement;
    expect(
      within(row).getByRole("button", {
        name: "I applied for Sapphire Preferred",
      }),
    ).toBeInTheDocument();
    for (const n of [
      "Move Sapphire Preferred down",
      "Edit Sapphire Preferred",
      "Drop Sapphire Preferred",
    ])
      expect(screen.queryByRole("button", { name: n })).toBeNull();
    await menu("Sapphire Preferred");
    expect(
      screen.getByRole("button", { name: "Edit Sapphire Preferred" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Move Sapphire Preferred up" }),
    ).toBeDisabled();
    await userEvent.keyboard("{Escape}");
    expect(
      screen.queryByRole("button", { name: "Edit Sapphire Preferred" }),
    ).toBeNull();
    expect(
      screen.getByRole("button", {
        name: "More actions for Sapphire Preferred",
      }),
    ).toHaveFocus();
  });

  it("brings a dropped item back with Want again", async () => {
    setup([wish({ id: 2, product: "Old one", status: "dropped" }), wish()]);
    await userEvent.click(screen.getByRole("button", { name: "Show" }));
    await userEvent.click(screen.getByRole("button", { name: "Want again" }));
    await waitFor(() =>
      expect(bodyOf(calls("/api/churning/wishlist/2")[0])).toEqual({
        status: "wanted",
      }),
    );
  });

  it("tells an applied item's Delete apart from the others: a link on the row, no menu", async () => {
    setup([
      wish(),
      wish({
        id: 3,
        product: "Got it",
        status: "applied",
        applied_on: "2026-09-01",
      }),
    ]);
    await userEvent.click(screen.getByRole("button", { name: "Show" }));
    expect(
      screen.queryByRole("button", { name: "More actions for Got it" }),
    ).toBeNull();
    await userEvent.click(
      screen.getByRole("button", { name: "Delete Got it" }),
    );
    expect(
      await screen.findByRole("dialog", { name: "Delete Got it?" }),
    ).toHaveTextContent("The card or bank bonus it became stays");
  });

  it("puts one person's plan between another's: one order for everyone", () => {
    setup([
      wish({ id: 1, owner: "Alex", product: "First", priority: 1 }),
      wish({ id: 2, owner: "Sam", product: "Second", priority: 2 }),
      wish({ id: 3, owner: "Alex", product: "Third", priority: 3 }),
    ]);
    expect(
      [...document.querySelectorAll("[data-wish]")].map((li) =>
        li.getAttribute("data-wish"),
      ),
    ).toEqual(["1", "2", "3"]);
    expect(
      screen.getAllByTitle("Priority").map((el) => el.textContent),
    ).toEqual(["#1", "#2", "#3"]);
  });

  it("moves an item up by renumbering priorities, and saves a score", async () => {
    const { onchanged } = setup([
      wish({ id: 1, priority: 1 }),
      wish({ id: 2, product: "Gold", priority: 2 }),
    ]);
    await menu("Gold");
    await userEvent.click(screen.getByRole("button", { name: "Move Gold up" }));
    await waitFor(() => expect(calls(/wishlist\/\d/)).toHaveLength(2));
    expect(calls(/wishlist\/\d/).map((c) => [c[0], bodyOf(c)])).toEqual([
      ["/api/churning/wishlist/2", { priority: 1 }],
      ["/api/churning/wishlist/1", { priority: 2 }],
    ]);
    await waitFor(() => expect(onchanged).toHaveBeenCalled());
  });

  it("shows credit scores only while a planned item wants one", () => {
    setup([wish({ min_score: null })]);
    expect(screen.queryByLabelText("Credit scores")).not.toBeInTheDocument();
  });

  it("saves a credit score", async () => {
    setup([wish({ min_score: 740 })]);
    await userEvent.click(
      screen.getAllByRole("button", { name: /credit score/ })[0],
    );
    await userEvent.type(screen.getByLabelText("Alex's score"), "720");
    await userEvent.click(screen.getByRole("button", { name: "Save score" }));
    await waitFor(() =>
      expect(bodyOf(calls("/api/churning/scores")[0])).toMatchObject({
        owner: "Alex",
        score: 720,
        as_of: "2026-09-30",
      }),
    );
  });
});
