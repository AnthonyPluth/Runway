// @vitest-environment jsdom
import { render, screen } from "@testing-library/svelte";
import { describe, expect, it } from "vitest";
import StatStrip from "./StatStrip.svelte";

describe("StatStrip", () => {
  it("shows each item's label, value and note", () => {
    render(StatStrip, { items: [{ label: "Budgeted", value: "$1,000", sub: "monthly" }, { label: "Other", value: "$50" }] });
    expect(screen.getByText("Budgeted")).toBeInTheDocument();
    expect(screen.getByText("$1,000")).toBeInTheDocument();
    expect(screen.getByText("monthly")).toBeInTheDocument();
    expect(screen.getByText("$50")).toBeInTheDocument();
    expect(screen.getAllByRole("term")).toHaveLength(2);
  });

  it("colors a bad or warning value and marks its label with a dot", () => {
    const { container } = render(StatStrip, { items: [{ label: "Lowest", value: "−$40", tone: "bad" }, { label: "Fees", value: "$95", tone: "warn" }, { label: "Plain", value: "$1" }] });
    expect(screen.getByText("−$40")).toHaveClass("text-destructive");
    expect(screen.getByText("$95")).toHaveClass("text-[var(--warning)]");
    expect(screen.getByText("$1")).not.toHaveClass("text-destructive");
    expect(container.querySelectorAll("dt > span[aria-hidden]")).toHaveLength(2);
  });

  it("colors a good value without a dot", () => {
    const { container } = render(StatStrip, { items: [{ label: "Left", value: "$9", tone: "good" }] });
    expect(screen.getByText("$9")).toHaveClass("text-emerald-400");
    expect(container.querySelector("dt > span[aria-hidden]")).toBeNull();
  });

  it("colors just the note with subTone, leaving the value plain", () => {
    render(StatStrip, { items: [{ label: "In 90 days", value: "$20,000", sub: "+$1,500", subTone: "good" }, { label: "Owed", value: "$10", sub: "1 card" }] });
    expect(screen.getByText("+$1,500")).toHaveClass("text-emerald-400");
    expect(screen.getByText("$20,000")).not.toHaveClass("text-emerald-400");
    expect(screen.getByText("1 card")).toHaveClass("text-muted-foreground");
  });

  it("lays out as a two-column grid on phones and compact tiles from md up", () => {
    const { container } = render(StatStrip, { items: [{ label: "A", value: "1" }] });
    expect(container.firstElementChild).toHaveClass("grid-cols-2", "md:grid-cols-[repeat(auto-fill,minmax(12rem,15.5rem))]");
  });
});
