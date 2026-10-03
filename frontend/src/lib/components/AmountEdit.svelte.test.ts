// @vitest-environment jsdom
import { render, screen } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import AmountEdit from "./AmountEdit.svelte";

const setup = (props: Partial<{ amount: number; signed: boolean }> = {}) => {
  const save = vi.fn().mockResolvedValue(undefined);
  const user = userEvent.setup();
  render(AmountEdit, { amount: -42.5, label: "Amount", title: "Change it", save, ...props });
  return { save, user };
};

describe("AmountEdit", () => {
  it("shows the amount as money", () => {
    setup();
    expect(screen.getByRole("button", { name: "-$42.50" })).toBeInTheDocument();
  });

  it("marks an incoming amount with + and a plain minus for an outgoing one when signed", () => {
    setup({ amount: 100, signed: true });
    const inc = screen.getByRole("button");
    expect(inc).toHaveTextContent("+$100.00");
    expect(inc).toHaveClass("text-good");
  });

  it("uses a real minus sign for an outgoing signed amount", () => {
    setup({ amount: -8, signed: true });
    expect(screen.getByRole("button")).toHaveTextContent("−$8.00");
  });

  it("opens an editor holding the positive number, focused, when clicked", async () => {
    const { user } = setup();
    await user.click(screen.getByRole("button"));
    const input = screen.getByRole("spinbutton", { name: "Amount" });
    expect(input).toHaveValue(42.5);
    expect(input).toHaveFocus();
  });

  it("saves the new positive value on Enter, leaving the caller to decide the sign", async () => {
    const { user, save } = setup();
    await user.click(screen.getByRole("button"));
    const input = screen.getByRole("spinbutton");
    await user.clear(input);
    await user.type(input, "60{Enter}");
    expect(save).toHaveBeenCalledExactlyOnceWith(60);
    expect(screen.queryByRole("spinbutton")).not.toBeInTheDocument();
  });

  it("saves when you click away", async () => {
    const { user, save } = setup();
    await user.click(screen.getByRole("button"));
    await user.clear(screen.getByRole("spinbutton"));
    await user.type(screen.getByRole("spinbutton"), "7");
    await user.tab();
    expect(save).toHaveBeenCalledExactlyOnceWith(7);
  });

  it("discards the edit on Escape", async () => {
    const { user, save } = setup();
    await user.click(screen.getByRole("button"));
    await user.type(screen.getByRole("spinbutton"), "9{Escape}");
    expect(save).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "-$42.50" })).toBeInTheDocument();
  });

  it("doesn't save an unchanged or emptied value", async () => {
    const { user, save } = setup();
    await user.click(screen.getByRole("button"));
    await user.type(screen.getByRole("spinbutton"), "{Enter}");
    await user.click(screen.getByRole("button"));
    await user.clear(screen.getByRole("spinbutton"));
    await user.type(screen.getByRole("spinbutton"), "{Enter}");
    expect(save).not.toHaveBeenCalled();
  });
});
