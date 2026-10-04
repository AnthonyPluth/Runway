// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({
  toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }),
}));

import { api } from "$lib/api";
import { toast } from "svelte-sonner";
import CardBenefits from "./CardBenefits.svelte";
import { benefit, bodyOf, calls, card, churning } from "./fixtures";

beforeEach(() => {
  vi.mocked(api).mockReset();
  vi.mocked(api).mockResolvedValue({ ok: true } as never);
  vi.mocked(toast).mockReset();
  vi.mocked(toast.error).mockReset();
});

describe("benefits", () => {
  const setup = (over = {}) => {
    const onchanged = vi.fn();
    const c = card({
      annual_fee: 395,
      benefits: [benefit(over)],
      benefits_value: 300,
      net_fee: 95,
    });
    render(CardBenefits, { card: c, d: churning(), onchanged });
    return onchanged;
  };

  it("says what's left this period and marks the rest used, with an undo", async () => {
    vi.mocked(api).mockResolvedValue({ id: 7 } as never);
    const onchanged = setup({ used: 100, remaining: 200, used_count: 1 });
    expect(screen.getByText(/\$100 of \$300 used/)).toBeInTheDocument();
    expect(
      screen.getByText(/Benefits \$300\/yr · net fee \$95/),
    ).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Mark Travel credit used" }),
    );
    await waitFor(() =>
      expect(calls("/api/churning/benefits/1/use")).toHaveLength(1),
    );
    expect(bodyOf(calls("/api/churning/benefits/1/use")[0])).toEqual({});
    expect(onchanged).toHaveBeenCalled();
    const opts = vi.mocked(toast).mock.calls.at(-1)![1] as {
      action: { label: string; onClick: () => void };
    };
    expect(opts.action.label).toBe("Undo");
    opts.action.onClick();
    await waitFor(() =>
      expect(bodyOf(calls("/api/churning/benefits/1/unuse")[0])).toEqual({
        use_id: 7,
      }),
    );
  });

  it("marks a partial amount used, and undoes the latest use from the button", async () => {
    vi.mocked(api).mockResolvedValue({ id: 8 } as never);
    setup({
      used: 100,
      remaining: 200,
      used_count: 1,
      uses: [{ id: 3, amount_used: 100, used_on: "2026-09-01" }],
    });
    await userEvent.type(
      screen.getByLabelText("Amount of Travel credit used (blank: the rest)"),
      "50",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Mark Travel credit used" }),
    );
    await waitFor(() =>
      expect(bodyOf(calls("/api/churning/benefits/1/use")[0])).toEqual({
        amount: 50,
      }),
    );
    await userEvent.click(
      screen.getByRole("button", {
        name: "Undo the last use of Travel credit",
      }),
    );
    await waitFor(() =>
      expect(bodyOf(calls("/api/churning/benefits/1/unuse")[0])).toEqual({
        use_id: 3,
      }),
    );
    const [msg, opts] = vi.mocked(toast).mock.calls.at(-1)! as unknown as [
      string,
      { action: { onClick: () => void } },
    ];
    expect(msg).toBe("Took the use off Travel credit");
    opts.action.onClick();
    await waitFor(() =>
      expect(bodyOf(calls("/api/churning/benefits/1/use")[1])).toEqual({
        used_on: "2026-09-01",
        amount: 100,
      }),
    );
    expect(screen.getByText(/1 use ·/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Show" }));
    expect(
      screen.getByRole("button", {
        name: /Undo the Sep.1 use of Travel credit/,
      }),
    ).toBeInTheDocument();
  });

  it("disables Mark used once a credit is all used, and adds a preset", async () => {
    vi.mocked(api).mockResolvedValue({ id: 9 } as never);
    setup({ used: 300, remaining: 0, used_count: 1 });
    expect(
      screen.getByRole("button", { name: "Mark Travel credit used" }),
    ).toBeDisabled();
    expect(screen.getByText(/All \$300 used/)).toBeInTheDocument();
    await userEvent.selectOptions(
      screen.getByLabelText("Add a benefit to Venture X"),
      "lounge",
    );
    await waitFor(() =>
      expect(bodyOf(calls("/api/churning/cards/1/benefits")[0])).toEqual({
        preset: "lounge",
      }),
    );
  });
});
