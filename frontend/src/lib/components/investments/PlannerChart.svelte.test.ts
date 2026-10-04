// @vitest-environment jsdom
import { render, screen, waitFor, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));

import PlannerChart from "./PlannerChart.svelte";
import { project } from "./planner";
import { plan, resetPlanner } from "../../../test/planner";

beforeEach(resetPlanner);
afterEach(() => vi.useRealTimers());

describe("PlannerChart", () => {
  const proj = project(plan(), 400000, 2026, [], 50);
  const setup2 = () => render(PlannerChart, { p: proj, names: ["You"] });

  it("draws the likely band, the median line and a retirement marker", () => {
    const { container } = setup2();
    expect(container.querySelectorAll("svg path").length).toBeGreaterThanOrEqual(4);   // band, high, low, median
    expect(screen.getByText("retirement")).toBeInTheDocument();
    expect(screen.getByRole("img")).toHaveAttribute("aria-label", expect.stringMatching(/median \$[\d,]+ at the end/));
  });

  it("gives an all-zero plan a $0 to $1k axis, with no repeated labels", () => {
    const zero = project(plan({ people: [{ name: "Ann", birth_year: 1986, retire_age: 65, savings: 0 }], spending: 0 }), 0, 2026, [], 20);
    const { container } = render(PlannerChart, { p: zero, names: ["You"] });
    const labels = [...container.querySelectorAll("svg text")].map((t) => t.textContent).filter((t) => t?.startsWith("$"));
    expect(labels.length).toBeGreaterThan(1);
    expect(new Set(labels).size).toBe(labels.length);
    expect(labels).toContain("$0");
    expect(labels).toContain("$1k");
  });

  it("labels the axis with the first person's age", () => {
    setup2();
    expect(screen.getByText("Your age · year")).toBeInTheDocument();
  });

  it("puts the calendar year under each age, and marks today where the plan starts", () => {
    const { container } = setup2();
    const years = [...container.querySelectorAll("text[data-year]")].map((t) => Number(t.textContent));
    expect(years.length).toBeGreaterThan(2);
    expect(years.every((y) => y >= 2026)).toBe(true);
    expect(screen.getByTestId("today-mark")).toHaveTextContent("today");
  });

  it("names the person when it isn't 'You'", () => {
    render(PlannerChart, { p: proj, names: ["Ann"] });
    expect(screen.getByText("Ann's age · year")).toBeInTheDocument();
  });

  it("has a table of every year", async () => {
    setup2();
    await userEvent.click(screen.getByText("Show as table"));
    const rows = screen.getAllByRole("row");
    expect(rows).toHaveLength(proj.years.length + 1);
    expect(within(rows[1]).getByText("2026")).toBeInTheDocument();
  });

  it("shows a readout for the year under the pointer", async () => {
    const { container } = setup2();
    const svg = container.querySelector("svg")!;
    svg.getBoundingClientRect = () => ({ left: 0, top: 0, right: 320, bottom: 260, width: 320, height: 260, x: 0, y: 0, toJSON() {} });
    await userEvent.hover(container.querySelector("rect")!);
    const rect = container.querySelector("rect")!;
    rect.dispatchEvent(new MouseEvent("mousemove", { clientX: 100, bubbles: true }));
    expect(await screen.findByText("Typical", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByText("Good markets", { selector: "div span" })).toBeInTheDocument();
    rect.dispatchEvent(new MouseEvent("mouseleave"));
    await waitFor(() => expect(screen.queryByText("Typical", { selector: "span" })).not.toBeInTheDocument());
  });
});
