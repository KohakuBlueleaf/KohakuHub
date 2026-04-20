import { describe, expect, it, vi } from "vitest";

import {
  clearRepoSortPreference,
  getRepoSortPreference,
  setRepoSortPreference,
} from "@/utils/repoSortPreference";

describe("repo sort preference utilities", () => {
  it("persists and validates sort preferences", () => {
    expect(
      getRepoSortPreference({
        scope: "user",
        repoType: "model",
      }),
    ).toBe("recent");

    setRepoSortPreference({
      scope: "user",
      repoType: "model",
      value: "likes",
    });

    expect(
      getRepoSortPreference({
        scope: "user",
        repoType: "model",
      }),
    ).toBe("likes");

    expect(
      getRepoSortPreference({
        scope: "user",
        repoType: "model",
        allowedValues: ["recent", "downloads"],
        fallback: "downloads",
      }),
    ).toBe("downloads");
  });

  it("clears invalid and scoped preferences", () => {
    setRepoSortPreference({
      scope: "user",
      repoType: "model",
      value: "invalid-sort",
    });

    expect(
      getRepoSortPreference({
        scope: "user",
        repoType: "model",
      }),
    ).toBe("recent");

    setRepoSortPreference({
      scope: "user",
      repoType: "model",
      value: "updated",
    });
    setRepoSortPreference({
      scope: "org",
      repoType: "dataset",
      value: "downloads",
    });

    clearRepoSortPreference();

    expect(sessionStorage.length).toBe(0);
  });

  it("falls back safely when window is unavailable", () => {
    vi.stubGlobal("window", undefined);

    expect(
      getRepoSortPreference({
        scope: "home",
        repoType: "all",
        fallback: "likes",
      }),
    ).toBe("likes");

    expect(() =>
      setRepoSortPreference({
        scope: "home",
        repoType: "all",
        value: "recent",
      }),
    ).not.toThrow();
    expect(() => clearRepoSortPreference()).not.toThrow();
  });
});
