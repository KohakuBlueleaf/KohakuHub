import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  DEFAULT_PAGE_SIZE,
  DEFAULT_VIEW_MODE,
  VALID_PAGE_SIZES,
  VALID_VIEW_MODES,
  readPageSize,
  readViewMode,
  writePageSize,
  writeViewMode,
} from "@/utils/tar-listing-prefs";

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  localStorage.clear();
});

describe("tar-listing-prefs · constants", () => {
  it("declares grid as the default view mode and 20 as the default page size", () => {
    expect(DEFAULT_VIEW_MODE).toBe("grid");
    expect(DEFAULT_PAGE_SIZE).toBe(20);
  });
  it("only accepts list/grid view modes", () => {
    expect(VALID_VIEW_MODES).toEqual(["list", "grid"]);
  });
  it("offers the three documented page-size buckets", () => {
    expect(VALID_PAGE_SIZES).toEqual([20, 50, 100]);
  });
});

describe("readViewMode / writeViewMode", () => {
  it("returns the default when localStorage has no opinion", () => {
    expect(readViewMode()).toBe("grid");
  });

  it("round-trips a valid value via localStorage", () => {
    writeViewMode("list");
    expect(readViewMode()).toBe("list");
    expect(localStorage.getItem("kohaku-tar-view-mode")).toBe("list");
  });

  it("ignores writes for invalid values and reads keep returning the default", () => {
    writeViewMode("compact"); // not in VALID_VIEW_MODES
    expect(localStorage.getItem("kohaku-tar-view-mode")).toBe(null);
    expect(readViewMode()).toBe("grid");
  });

  it("falls back to the default when storage carries an invalid value", () => {
    localStorage.setItem("kohaku-tar-view-mode", "weird");
    expect(readViewMode()).toBe("grid");
  });
});

describe("readPageSize / writePageSize", () => {
  it("returns the default when localStorage has no opinion", () => {
    expect(readPageSize()).toBe(20);
  });

  it("round-trips each documented bucket", () => {
    for (const v of VALID_PAGE_SIZES) {
      writePageSize(v);
      expect(readPageSize()).toBe(v);
    }
  });

  it("coerces stored strings back to numbers on read", () => {
    localStorage.setItem("kohaku-tar-page-size", "50");
    expect(readPageSize()).toBe(50);
  });

  it("ignores writes for sizes not in the bucket list", () => {
    writePageSize(37);
    expect(localStorage.getItem("kohaku-tar-page-size")).toBe(null);
    expect(readPageSize()).toBe(20);
  });

  it("falls back to the default when storage carries garbage", () => {
    localStorage.setItem("kohaku-tar-page-size", "abc");
    expect(readPageSize()).toBe(20);
    localStorage.setItem("kohaku-tar-page-size", "999");
    expect(readPageSize()).toBe(20);
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
      expect(readViewMode()).toBe("grid");
      expect(readPageSize()).toBe(20);
      writeViewMode("list");
      writePageSize(50);
      // No throw — both calls swallow the error and the runtime
      // returns to the in-memory defaults on the next read.
    } finally {
      Object.defineProperty(globalThis, "localStorage", {
        configurable: true,
        value: realStorage,
      });
    }
  });
});
