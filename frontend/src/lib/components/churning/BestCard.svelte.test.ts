// @vitest-environment jsdom
import { render, screen } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));
vi.mock("$lib/categories.svelte", () => ({ loadCategories: vi.fn(async () => {}), categories: { list: [] }, catLabel: (x: string) => x, categoryGroups: () => [] }));

import { api } from "$lib/api";
import BestCard from "./BestCard.svelte";
import { calls } from "./fixtures";

beforeEach(() => {
  vi.mocked(api).mockReset();
  vi.mocked(api).mockResolvedValue({ ok: true } as never);
});

describe("best card", () => {
  it("asks for portal rates when you'll book through the portal, and shows the portal note", async () => {
    const row = { id: 1, owner: "Alex", product: "Venture X", issuer: "capital_one", multiplier: 10, currency: "Capital One miles", cents: 1.4,
      return_pct: 14, value: null, bonus: null, needs_portal: true, portal_name: "Capital One Travel", portal_option: null,
      note: "Only when booked through Capital One Travel" };
    vi.mocked(api).mockImplementation((async (path: string) => ({ cards: [path.includes("portal=1") ? row : { ...row, multiplier: 2, needs_portal: false, note: null }] })) as never);
    render(BestCard, { person: "", version: 0, showOwner: false });
    await screen.findByText("Venture X");
    expect(calls(/best\?/).at(-1)![0]).not.toContain("portal=1");
    await userEvent.click(screen.getByLabelText("I'll book through the issuer's travel portal"));
    expect(await screen.findByText("Only when booked through Capital One Travel")).toBeInTheDocument();
    expect(screen.getByText("Via Capital One Travel")).toBeInTheDocument();
    expect(calls(/best\?/).at(-1)![0]).toContain("portal=1");
  });

  it("offers the better portal rate when portal isn't ticked", async () => {
    const row = { id: 1, owner: "Alex", product: "Venture X", issuer: "capital_one", multiplier: 2, currency: "miles", cents: 1.4, return_pct: 2.8,
      value: null, bonus: null, needs_portal: false, portal_name: "Capital One Travel",
      portal_option: { multiplier: 10, return_pct: 14, portal_name: "Capital One Travel", value: null, note: "" }, note: "10x if booked through Capital One Travel" };
    vi.mocked(api).mockResolvedValue({ cards: [row] } as never);
    render(BestCard, { person: "", version: 0, showOwner: false });
    expect(await screen.findByText("10x if booked through Capital One Travel")).toBeInTheDocument();
  });
});
