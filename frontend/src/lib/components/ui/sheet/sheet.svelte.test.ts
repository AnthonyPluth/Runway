// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import SheetHarness from "./SheetHarness.test.svelte";

describe("Sheet", () => {
  it("opens from its trigger and closes with Esc", async () => {
    const onOpenChange = vi.fn();
    render(SheetHarness, { onOpenChange });
    const user = userEvent.setup();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog", { name: "Edit asset" });
    expect(dialog).toHaveAccessibleDescription("Change the details.");
    expect(onOpenChange).toHaveBeenLastCalledWith(true);
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(onOpenChange).toHaveBeenLastCalledWith(false);
  });

  it("closes from Sheet.Close and honours an initial open", async () => {
    render(SheetHarness, { open: true });
    const user = userEvent.setup();
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});
