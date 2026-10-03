import { describe, expect, it } from "vitest";
import { SECTIONS, resolveSection } from "./sections";

describe("Settings tabs", () => {
  it("lists Accounts first, then Connections (which holds the optional services too), and Data last", () => {
    expect(SECTIONS.map((s) => s.id)).toEqual(["accounts", "connections", "categories", "rules", "notifications", "data"]);
    expect(SECTIONS.find((s) => s.id === "connections")?.label).toBe("Connections");
  });

  it("sends the old #setup/advanced and #setup/backup links to Data, and #setup/extension and #setup/services to Connections, and keeps the other ids", () => {
    expect(SECTIONS.find((s) => s.id === "data")?.label).toBe("Data");
    expect(resolveSection("advanced", true)).toBe("data");
    expect(resolveSection("advanced", false)).toBe("data");
    expect(resolveSection("backup", true)).toBe("data");
    expect(resolveSection("data", true)).toBe("data");
    expect(resolveSection("extension", true)).toBe("connections");
    expect(resolveSection("connections", true)).toBe("connections");
    expect(resolveSection("services", false)).toBe("connections");
  });

  it("opens on Accounts, or on Connections until a bank is connected", () => {
    expect(resolveSection("", true)).toBe("accounts");
    expect(resolveSection("", false)).toBe("connections");
    expect(resolveSection("nonsense", false)).toBe("connections");
  });
});
