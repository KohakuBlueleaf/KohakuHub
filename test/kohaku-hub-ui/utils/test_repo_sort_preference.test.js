import { describe, expect, it } from "vitest";

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
});
