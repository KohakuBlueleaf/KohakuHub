import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  addExternalToken,
  clearExternalTokens,
  formatAuthHeader,
  getExternalTokens,
  removeExternalToken,
  setExternalTokens,
} from "@/utils/externalTokens";

describe("external token utilities", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("stores, updates, and removes tokens", () => {
    expect(getExternalTokens()).toEqual([]);

    addExternalToken("https://hf.example.com", "token-a");
    addExternalToken("https://fallback.example.com", "token-b");
    addExternalToken("https://hf.example.com", "token-a2");

    expect(getExternalTokens()).toEqual([
      { url: "https://hf.example.com", token: "token-a2" },
      { url: "https://fallback.example.com", token: "token-b" },
    ]);

    removeExternalToken("https://hf.example.com");
    expect(getExternalTokens()).toEqual([
      { url: "https://fallback.example.com", token: "token-b" },
    ]);

    clearExternalTokens();
    expect(getExternalTokens()).toEqual([]);
  });

  it("returns empty tokens on parse failure and keeps format stable", () => {
    localStorage.setItem("hf_external_tokens", "{bad-json");
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});

    expect(getExternalTokens()).toEqual([]);
    expect(formatAuthHeader("main-token", [
      { url: "https://a.example.com", token: "x" },
      { url: "https://b.example.com", token: "" },
    ])).toBe("Bearer main-token|https://a.example.com,x|https://b.example.com,");

    setExternalTokens([{ url: "https://c.example.com", token: "z" }]);
    expect(getExternalTokens()).toEqual([
      { url: "https://c.example.com", token: "z" },
    ]);

    errorSpy.mockRestore();
  });
});
