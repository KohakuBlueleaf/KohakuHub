import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  DEFAULT_PAGE_SIZE,
  VALID_PAGE_SIZES,
  readPageSize,
  writePageSize,
} from "@/utils/repo-list-pagination";

const STORAGE_KEY = "kohaku-repo-file-list-page-size";

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  localStorage.clear();
});

describe("repo-list-pagination · constants", () => {
  it("defaults to 50 entries per page", () => {
    expect(DEFAULT_PAGE_SIZE).toBe(50);
  });

  it("only accepts the documented page-size buckets", () => {
    expect(VALID_PAGE_SIZES).toEqual([50, 100, 200]);
  });
});

describe("readPageSize / writePageSize", () => {
  it("returns the default when localStorage has no opinion", () => {
    expect(readPageSize()).toBe(50);
  });

  it("round-trips each documented bucket", () => {
    for (const v of VALID_PAGE_SIZES) {
      writePageSize(v);
      expect(readPageSize()).toBe(v);
    }
  });

  it("coerces stored strings back to numbers on read", () => {
    localStorage.setItem(STORAGE_KEY, "100");
    expect(readPageSize()).toBe(100);
  });

  it("ignores writes for sizes not in the bucket list", () => {
    writePageSize(75);
    expect(localStorage.getItem(STORAGE_KEY)).toBe(null);
    expect(readPageSize()).toBe(50);
  });

  it("falls back to the default when storage carries garbage", () => {
    localStorage.setItem(STORAGE_KEY, "abc");
    expect(readPageSize()).toBe(50);
    localStorage.setItem(STORAGE_KEY, "999");
    expect(readPageSize()).toBe(50);
  });

  it("accepts numeric strings on write (Element Plus el-select hands strings)", () => {
    writePageSize("200");
    expect(localStorage.getItem(STORAGE_KEY)).toBe("200");
    expect(readPageSize()).toBe(200);
  });
});

describe("safe-storage fallback", () => {
  it("survives localStorage being unavailable", () => {
    const realStorage = globalThis.localStorage;
    Object.defineProperty(globalThis, "localStorage", {
      configurable: true,
      get() {
        throw new Error("storage disabled");
      },
    });
    try {
      expect(readPageSize()).toBe(50);
      writePageSize(100);
      // No throw — both calls swallow the error and the runtime
      // returns to the in-memory default on the next read.
    } finally {
      Object.defineProperty(globalThis, "localStorage", {
        configurable: true,
        value: realStorage,
      });
    }
  });
});
