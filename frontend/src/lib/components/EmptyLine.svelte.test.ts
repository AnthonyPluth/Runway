// @vitest-environment jsdom
import { render, screen } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import EmptyLine from "./EmptyLine.svelte";

describe("EmptyLine", () => {
  it("shows the label and message on one muted line", () => {
    const { container } = render(EmptyLine, { label: "Upcoming", message: "nothing in the next six months" });
    expect(screen.getByText("Upcoming")).toBeInTheDocument();
    expect(screen.getByText("nothing in the next six months")).toBeInTheDocument();
    expect(container.querySelector("p")).toHaveClass("text-muted-foreground");
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("runs the inline action when clicked", async () => {
    const onaction = vi.fn();
    render(EmptyLine, { label: "Upcoming", message: "nothing", action: "Add a to-do", onaction });
    await userEvent.click(screen.getByRole("button", { name: "Add a to-do" }));
    expect(onaction).toHaveBeenCalledOnce();
  });

  it("leaves out the action when there is no handler", () => {
    render(EmptyLine, { label: "Upcoming", message: "nothing", action: "Add a to-do" });
    expect(screen.queryByRole("button")).toBeNull();
  });
});
