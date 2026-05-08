// src/kohaku-hub-ui/src/stores/theme.js
import { defineStore, acceptHMRUpdate } from "pinia";

export const useThemeStore = defineStore("theme", {
  state: () => ({
    isDark: localStorage.getItem("theme") === "dark" || false,
  }),

  actions: {
    /**
     * Toggle between light and dark mode
     */
    toggle() {
      this.isDark = !this.isDark;
      this.apply();
    },

    /**
     * Set theme mode
     * @param {boolean} isDark - Whether to enable dark mode
     */
    setTheme(isDark) {
      this.isDark = isDark;
      this.apply();
    },

    /**
     * Apply theme to document and save to localStorage
     */
    apply() {
      const html = document.documentElement;

      if (this.isDark) {
        html.classList.add("dark");
        localStorage.setItem("theme", "dark");
      } else {
        html.classList.remove("dark");
        localStorage.setItem("theme", "light");
      }
    },

    /**
     * Initialize theme on app load
     */
    init() {
      this.apply();
    },
  },
});

// Hot-replace this store on edit instead of full-reloading the page.
if (import.meta.hot) {
  import.meta.hot.accept(acceptHMRUpdate(useThemeStore, import.meta.hot));
}
