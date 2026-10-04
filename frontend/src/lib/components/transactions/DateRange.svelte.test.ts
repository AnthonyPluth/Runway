// @vitest-environment jsdom
import { render, screen } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import DateRange from "./DateRange.svelte";

const open = async (props: { from?: string; to?: string } = {}) => {
  const onchange = vi.fn();
  const user = userEvent.setup();
  render(DateRange, { props: { from: "", to: "", ...props, onchange } });
  await user.click(screen.getByRole("button", { name: /^Dates:/ }));
  return { user, onchange, from: screen.getByLabelText(/^From/) as HTMLInputElement, to: screen.getByLabelText(/^To/) as HTMLInputElement };
};
const inPopover = { hidden: true };
const inside = (text: string) => screen.getByText(text, { selector: "[data-popover-content] button" });
const apply = () => inside("Apply");

describe("DateRange", () => {
  it("offers the coming months as well as the past", async () => {
    await open();
    for (const name of ["This month", "Last month", "Next month", "Next 3 months", "This year", "All time"]) expect(inside(name)).toBeInTheDocument();
  });

  it("says what an empty date field is, and drops the hint once it has a date", async () => {
    const { user, from } = await open();
    expect(screen.getByText("Start")).toBeInTheDocument();
    expect(screen.getByText("End")).toBeInTheDocument();
    await user.type(from, "2026-11-01");
    expect(screen.queryByText("Start")).not.toBeInTheDocument();
    expect(screen.getByText("End")).toBeInTheDocument();
  });

  it("shows no hint for a field that has a date", async () => {
    await open({ from: "2026-03-01", to: "2026-03-31" });
    expect(screen.queryByText("Start")).not.toBeInTheDocument();
    expect(screen.queryByText("End")).not.toBeInTheDocument();
  });

  it("puts no limit on the dates", async () => {
    const { from, to } = await open();
    expect(from).not.toHaveAttribute("max");
    expect(to).not.toHaveAttribute("max");
  });

  it("enables Apply as soon as a field changes", async () => {
    const { user, from } = await open({ from: "2026-03-01", to: "2026-03-31" });
    expect(apply()).toBeDisabled();
    await user.clear(from);
    expect(apply()).toBeEnabled();
  });

  it("passes a future range on", async () => {
    const { user, onchange, from, to } = await open();
    await user.type(from, "2027-01-01");
    await user.type(to, "2027-06-30");
    await user.click(apply());
    expect(onchange).toHaveBeenCalledWith("2027-01-01", "2027-06-30");
  });

  it("takes just a start or just an end", async () => {
    const first = await open();
    await first.user.type(first.to, "2027-02-10");
    await first.user.click(apply());
    expect(first.onchange).toHaveBeenCalledWith("", "2027-02-10");
  });

  it("won't apply an end before the start", async () => {
    const { user, onchange, from, to } = await open();
    await user.type(from, "2026-12-01");
    await user.type(to, "2026-11-01");
    expect(screen.getByRole("alert", inPopover)).toHaveTextContent("The end is before the start.");
    expect(apply()).toBeDisabled();
    expect(onchange).not.toHaveBeenCalled();
  });

  it("picks a forward preset in one tap", async () => {
    const { user, onchange } = await open();
    await user.click(inside("Next month"));
    expect(onchange).toHaveBeenCalledTimes(1);
    const [f, t] = onchange.mock.calls[0];
    expect(f < t && f.endsWith("-01")).toBe(true);
  });
});
