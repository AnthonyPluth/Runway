// @vitest-environment jsdom
import { render, screen } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import AiButton from "./AiButton.svelte";

describe("AiButton", () => {
  it("shows the sparkles icon and the label, and calls onclick", async () => {
    const onclick = vi.fn();
    render(AiButton, { label: "Suggest budgets", onclick });
    const button = screen.getByRole("button", { name: "Suggest budgets" });
    expect(button.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    expect(button).toHaveTextContent("Suggest budgets");
    await userEvent.click(button);
    expect(onclick).toHaveBeenCalledOnce();
  });

  it("hides the label on a phone but keeps it as the accessible name", () => {
    render(AiButton, { label: "Ask again", onclick: vi.fn() });
    expect(screen.getByText("Ask again")).toHaveClass("hidden", "sm:inline");
    expect(screen.getByRole("button", { name: "Ask again" })).toHaveClass("size-10", "sm:w-auto");
  });

  it("swaps to the busy label and can't be clicked while busy", async () => {
    const onclick = vi.fn();
    render(AiButton, { label: "Suggest categories", busyLabel: "Suggesting…", busy: true, onclick });
    const button = screen.getByRole("button", { name: "Suggesting…" });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");
    await userEvent.click(button);
    expect(onclick).not.toHaveBeenCalled();
  });

  it("is disabled on request and passes the tooltip through", () => {
    render(AiButton, { label: "Fill in the rest", disabled: true, title: "Asks an AI model", onclick: vi.fn() });
    const button = screen.getByRole("button", { name: "Fill in the rest" });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("title", "Asks an AI model");
    expect(button).not.toHaveAttribute("aria-busy");
  });
});
