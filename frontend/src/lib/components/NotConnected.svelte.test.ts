// @vitest-environment jsdom
import { render, screen } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import NotConnected from "./NotConnected.svelte";

describe("NotConnected", () => {
  it("says what the page needs and links to connecting a bank", () => {
    render(NotConnected, { title: "Connect a bank to see your transactions" });
    expect(screen.getByText("Connect a bank to see your transactions")).toBeInTheDocument();
    expect(screen.queryByText(/months of history/)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Connect a bank" })).toHaveAttribute("href", "#setup/connections");
  });

  it("takes its own sentence", () => {
    render(NotConnected, { title: "T", text: "Something specific." });
    expect(screen.getByText("Something specific.")).toBeInTheDocument();
    expect(screen.queryByText(/months of history/)).not.toBeInTheDocument();
  });

  it("offers no second action unless the page gives one, and runs it when clicked", async () => {
    const { unmount } = render(NotConnected, { title: "T" });
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    unmount();
    const onclick = vi.fn();
    render(NotConnected, { title: "T", secondary: { label: "Add an asset by hand", onclick } });
    await userEvent.click(screen.getByRole("button", { name: "Add an asset by hand" }));
    expect(onclick).toHaveBeenCalledOnce();
  });
});
