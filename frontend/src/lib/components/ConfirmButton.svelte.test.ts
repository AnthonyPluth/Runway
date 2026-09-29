// @vitest-environment jsdom
import { render, screen } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { createRawSnippet } from "svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ConfirmButton from "./ConfirmButton.svelte";

const label = createRawSnippet(() => ({ render: () => "<span>Remove</span>" }));

describe("ConfirmButton", () => {
  beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
  afterEach(() => vi.useRealTimers());

  it("asks first and only acts on the second click", async () => {
    const onconfirm = vi.fn();
    render(ConfirmButton, { confirm: "Really?", onconfirm, children: label });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    await user.click(screen.getByRole("button", { name: "Remove" }));
    expect(onconfirm).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Really?" }));
    expect(onconfirm).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "Remove" })).toBeInTheDocument();
  });

  it("goes back to its label after 4 seconds without a second click", async () => {
    const onconfirm = vi.fn();
    render(ConfirmButton, { confirm: "Really?", onconfirm, children: label });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    await user.click(screen.getByRole("button"));
    expect(screen.getByRole("button", { name: "Really?" })).toBeInTheDocument();
    await vi.advanceTimersByTimeAsync(4100);
    expect(screen.getByRole("button", { name: "Remove" })).toBeInTheDocument();
    await user.click(screen.getByRole("button"));   // a click after it lapsed asks again rather than acting
    expect(onconfirm).not.toHaveBeenCalled();
  });
});
