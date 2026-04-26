import { describe, expect, it } from "vitest";

import { hasCleanTags, parseTags } from "@/utils/tag-parser";

describe("tag parser", () => {
  it("extracts dataset tags and removes metadata prefixes", () => {
    const parsed = parseTags([
      "dataset:org/demo-dataset",
      "license:mit",
      "language:en",
      "custom-tag",
      "featured",
    ]);

    expect(parsed.datasets).toEqual(["org/demo-dataset"]);
    expect(parsed.cleanTags).toEqual(["custom-tag", "featured"]);
  });

  it("handles empty and invalid inputs safely", () => {
    expect(parseTags(null)).toEqual({ datasets: [], cleanTags: [] });
    expect(parseTags("not-an-array")).toEqual({ datasets: [], cleanTags: [] });
    expect(hasCleanTags(["pipeline:text-generation"])).toBe(false);
    expect(hasCleanTags(["community"])).toBe(true);
  });
});
