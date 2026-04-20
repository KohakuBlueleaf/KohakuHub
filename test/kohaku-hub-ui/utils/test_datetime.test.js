import { describe, expect, it, vi } from "vitest";

import {
  formatRelativeTime,
  formatUnixRelativeTime,
  initializeBrowserTimezone,
} from "@/utils/datetime";

describe("datetime utilities", () => {
  it("initializes browser timezone from Intl", () => {
    const resolvedOptions = vi.fn(() => ({ timeZone: "Asia/Shanghai" }));
    vi.stubGlobal("Intl", {
      DateTimeFormat: vi.fn(() => ({ resolvedOptions })),
    });

    expect(initializeBrowserTimezone()).toBe("Asia/Shanghai");
  });

  it("formats relative values and uses fallbacks for empty inputs", () => {
    expect(formatRelativeTime(null, "never")).toBe("never");
    expect(formatUnixRelativeTime(null, "Unknown")).toBe("Unknown");

    const iso = new Date(Date.now() - 60_000).toISOString();
    const formatted = formatRelativeTime(iso, "never");
    expect(typeof formatted).toBe("string");
    expect(formatted.length).toBeGreaterThan(0);

    const unixFormatted = formatUnixRelativeTime(
      Math.floor(Date.now() / 1000) - 120,
      "Unknown",
    );
    expect(typeof unixFormatted).toBe("string");
    expect(unixFormatted.length).toBeGreaterThan(0);
  });
});
