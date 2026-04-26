import { describe, expect, it } from "vitest";

import {
  normalizeCatchAllParam,
  resolveRepoTreeEntryPath,
} from "@/utils/repo-paths";

describe("repo path utilities", () => {
  it("normalizes catch-all params from router arrays and strings", () => {
    expect(normalizeCatchAllParam("metadata/features.json")).toBe(
      "metadata/features.json",
    );
    expect(normalizeCatchAllParam(["metadata", "features.json"])).toBe(
      "metadata/features.json",
    );
    expect(normalizeCatchAllParam(["", "/catalog/", "section-01/"])).toBe(
      "catalog/section-01",
    );
    expect(normalizeCatchAllParam(undefined)).toBe("");
  });

  it("does not duplicate repo-root paths returned by the tree API", () => {
    expect(resolveRepoTreeEntryPath("metadata", "metadata/features.json")).toBe(
      "metadata/features.json",
    );
    expect(resolveRepoTreeEntryPath("catalog", "catalog/section-01")).toBe(
      "catalog/section-01",
    );
    expect(resolveRepoTreeEntryPath("catalog", "section-01")).toBe(
      "catalog/section-01",
    );
  });
});
