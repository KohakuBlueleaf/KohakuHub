import { beforeEach, describe, expect, it } from "vitest";

describe("theme store", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.className = "";
  });

  async function createStore() {
    const piniaModule = await import("pinia");
    piniaModule.setActivePinia(piniaModule.createPinia());
    const themeModule = await import("@/stores/theme");
    return themeModule.useThemeStore();
  }

  it("initializes from persisted dark mode and applies the class", async () => {
    localStorage.setItem("theme", "dark");

    const store = await createStore();
    store.init();

    expect(store.isDark).toBe(true);
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });

  it("toggles and explicitly sets the theme", async () => {
    const store = await createStore();

    store.toggle();
    expect(store.isDark).toBe(true);
    expect(localStorage.getItem("theme")).toBe("dark");

    store.setTheme(false);
    expect(store.isDark).toBe(false);
    expect(document.documentElement.classList.contains("dark")).toBe(false);
    expect(localStorage.getItem("theme")).toBe("light");
  });
});
