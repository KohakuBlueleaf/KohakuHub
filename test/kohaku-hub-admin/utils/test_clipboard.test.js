import { beforeEach, describe, expect, it, vi } from "vitest";

import { copyToClipboard } from "@/utils/clipboard";

describe("admin clipboard utilities", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("uses the Clipboard API when available", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", {
      clipboard: { writeText },
    });

    await expect(copyToClipboard("kohakuhub-admin")).resolves.toBe(true);
    expect(writeText).toHaveBeenCalledWith("kohakuhub-admin");
  });

  it("returns false and logs when clipboard writes fail", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const writeText = vi.fn().mockRejectedValue(new Error("denied"));

    vi.stubGlobal("navigator", {
      clipboard: { writeText },
    });

    await expect(copyToClipboard("blocked")).resolves.toBe(false);
    expect(errorSpy).toHaveBeenCalled();
  });
});
