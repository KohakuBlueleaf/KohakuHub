import { describe, expect, it, vi } from "vitest";

import {
  formatRelativeTime,
  formatUnixRelativeTime,
  initializeBrowserTimezone,
} from "@/utils/datetime";

describe("datetime utilities", () => {
  function stubIntlTimeZone(timeZone) {
    const RealDateTimeFormat = globalThis.Intl.DateTimeFormat;

    vi.stubGlobal("Intl", {
      ...globalThis.Intl,
      DateTimeFormat: vi.fn((...args) => {
        const formatter = new RealDateTimeFormat(...args);
        return {
          format: formatter.format.bind(formatter),
          formatToParts: formatter.formatToParts.bind(formatter),
          resolvedOptions: () => ({
            ...formatter.resolvedOptions(),
            timeZone,
          }),
        };
      }),
    });
  }

  it("initializes browser timezone from Intl and falls back to dayjs guess", () => {
    stubIntlTimeZone("");

    expect(initializeBrowserTimezone()).not.toBe("");
  });

  it("keeps the last known timezone when Intl is unavailable", () => {
    stubIntlTimeZone("Asia/Shanghai");
    expect(initializeBrowserTimezone()).toBe("Asia/Shanghai");

    vi.stubGlobal("Intl", undefined);

    expect(initializeBrowserTimezone()).toBe("Asia/Shanghai");
  });

  it("formats relative values from utc strings and unsupported inputs", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-01T01:00:00Z"));
    stubIntlTimeZone("Asia/Shanghai");

    try {
      expect(formatRelativeTime("2026-01-01T08:30:00Z", "never")).toContain(
        "ago",
      );
      expect(formatRelativeTime({ value: "invalid" }, "never")).toBe("never");
      expect(formatRelativeTime(null, "never")).toBe("never");
      expect(formatUnixRelativeTime(0, "Unknown")).toBe("Unknown");
      expect(
        formatUnixRelativeTime(Math.floor(Date.now() / 1000) - 120),
      ).toContain("ago");
    } finally {
      vi.useRealTimers();
    }
  });

  it("formats date objects, local strings, and past utc strings", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-01T10:00:00Z"));
    stubIntlTimeZone("Asia/Shanghai");

    try {
      expect(
        formatRelativeTime(new Date("2026-01-01T09:55:00Z"), "never"),
      ).toContain("ago");
      expect(formatRelativeTime("2026-01-01 17:30:00", "never")).toContain(
        "ago",
      );
      expect(formatRelativeTime("2026-01-01T00:30:00Z", "never")).toContain(
        "ago",
      );
    } finally {
      vi.useRealTimers();
    }
  });
});
