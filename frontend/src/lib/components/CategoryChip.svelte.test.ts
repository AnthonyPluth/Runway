// @vitest-environment jsdom
import { render, screen } from "@testing-library/svelte";
import { beforeEach, describe, expect, it } from "vitest";
import { categories } from "$lib/categories.svelte";
import type { Category } from "$lib/types";
import CategoryChip, { chipButton } from "./CategoryChip.svelte";

const cat = (name: string, extra: Partial<Category> = {}): Category => ({ name, path: [name], depth: 0, top: name, ...extra });
beforeEach(() => {
  categories.list = [
    cat("Groceries", { icon: "🛒" }),
    cat("Snacks & Sweets", { icon: "🍪", parent: "Groceries", path: ["Groceries", "Snacks & Sweets"], depth: 1, top: "Groceries" }),
  ];
});

describe("CategoryChip", () => {
  it("shows the emoji first, then only the child's name, and keeps the full path as the tooltip", () => {
    render(CategoryChip, { category: "Snacks & Sweets" });
    const emoji = screen.getByText("🍪"), name = screen.getByText("Snacks & Sweets");
    expect(emoji).toHaveAttribute("aria-hidden", "true");
    expect(emoji.compareDocumentPosition(name) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(name).toHaveAttribute("title", "Groceries > Snacks & Sweets");
    expect(name.parentElement).toHaveTextContent(/^🍪 Snacks & Sweets$/);
    expect(name.parentElement!.textContent).not.toContain(">");
  });

  it("is a filled pill with a hairline border in the light theme, 28px tall (24px in a receipt), and no chevron", () => {
    const { container, rerender } = render(CategoryChip, { category: "Groceries" });
    const pill = container.firstElementChild!;
    expect(pill).toHaveClass("rounded-full", "bg-muted", "text-foreground", "text-[13px]", "h-7", "border-border", "dark:border-transparent");
    expect(container.querySelector("svg")).toBeNull();
    rerender({ category: "Groceries", size: "receipt" });
    expect(container.firstElementChild).toHaveClass("h-6");
  });

  it("truncates a long name with an ellipsis", () => {
    render(CategoryChip, { category: "Groceries" });
    expect(screen.getByText("Groceries")).toHaveClass("truncate");
    expect(screen.getByText("Groceries").parentElement).toHaveClass("min-w-0", "max-w-full");
  });

  it("is a dashed amber 'Choose category' with nothing chosen", () => {
    const { container } = render(CategoryChip, { category: "" });
    expect(container.firstElementChild).toHaveClass("border-dashed", "text-warning");
    expect(container).toHaveTextContent("Choose category");
    expect(container.querySelector("[aria-hidden]")).toBeNull();
  });

  it("can say something else with nothing chosen, and write its own text without an emoji", () => {
    const first = render(CategoryChip, { category: null, empty: "Choose…" });
    expect(first.container).toHaveTextContent("Choose…");
    first.unmount();
    const second = render(CategoryChip, { category: "", text: "Groceries, Coffee" });
    expect(second.container).toHaveTextContent(/^Groceries, Coffee$/);
  });

  it("gives the button an invisible hit area that grows the tap target to 44px on a phone", () => {
    expect(chipButton("row")).toContain("phone:before:-inset-y-2");
    expect(chipButton("receipt")).toContain("phone:before:-inset-y-2.5");
    expect(chipButton("row")).not.toContain("size-");
  });
});
