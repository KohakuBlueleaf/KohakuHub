import { describe, expect, it } from "vitest";

import {
  formatMetadataKey,
  formatSizeCategory,
  getLanguageName,
  getLicenseName,
  getPipelineTagName,
  getStandardLicenseLink,
} from "@/utils/metadata-helpers";

describe("metadata helpers", () => {
  it("maps known languages and falls back for unknown ones", () => {
    expect(getLanguageName("en")).toBe("English");
    expect(getLanguageName("multilingual")).toBe("Multilingual");
    expect(getLanguageName("zz")).toBe("ZZ");
  });

  it("returns standard license names and links", () => {
    expect(getLicenseName("mit")).toBe("MIT License");
    expect(getLicenseName("custom-license")).toBe("CUSTOM-LICENSE");
    expect(getStandardLicenseLink("apache-2.0")).toBe(
      "https://www.apache.org/licenses/LICENSE-2.0",
    );
    expect(getStandardLicenseLink("unknown")).toBeNull();
  });

  it("formats metadata keys, pipeline tags, and size categories", () => {
    expect(formatMetadataKey("pipeline_tag")).toBe("Pipeline Tag");
    expect(getPipelineTagName("text-generation")).toBe("Text Generation");
    expect(getPipelineTagName("custom_task")).toBe("Custom Task");
    expect(formatSizeCategory("1K<n<10K")).toBe("1K < N < 10K");
  });
});
