import { beforeEach, describe, expect, it, vi } from "vitest";

import { copyToClipboard } from "@/utils/clipboard";

describe("clipboard utilities", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    document.body.innerHTML = "";
  });

  it("uses the Clipboard API when available", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", {
      clipboard: { writeText },
    });

    await expect(copyToClipboard("kohakuhub")).resolves.toBe(true);
    expect(writeText).toHaveBeenCalledWith("kohakuhub");
  });

  it("falls back to execCommand when the Clipboard API fails", async () => {
    const writeText = vi.fn().mockRejectedValue(new Error("denied"));
    const execCommand = vi.fn().mockReturnValue(true);

    vi.stubGlobal("navigator", {
      clipboard: { writeText },
    });
    Object.defineProperty(document, "execCommand", {
      configurable: true,
      value: execCommand,
    });

    await expect(copyToClipboard("fallback")).resolves.toBe(true);
    expect(execCommand).toHaveBeenCalledWith("copy");
    expect(document.querySelector("textarea")).toBeNull();
  });

  it("returns false when the fallback copy path throws", async () => {
    vi.stubGlobal("navigator", {});
    Object.defineProperty(document, "execCommand", {
      configurable: true,
      value: () => {
        throw new Error("copy failed");
      },
    });

    await expect(copyToClipboard("broken")).resolves.toBe(false);
    expect(document.querySelector("textarea")).toBeNull();
  });
});
