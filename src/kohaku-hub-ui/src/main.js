// src/kohaku-hub-ui/src/main.js
import { createApp } from "vue";
import { createPinia } from "pinia";
import { createRouter, createWebHistory } from "vue-router";
import { routes } from "vue-router/auto-routes";
import App from "./App.vue";
import { initializeBrowserTimezone } from "./utils/datetime";

// Import UnoCSS
import "virtual:uno.css";
import "@unocss/reset/tailwind.css";

// Import Element Plus base styles
import "element-plus/dist/index.css";
// Import Element Plus dark theme
import "element-plus/theme-chalk/dark/css-vars.css";

// Import custom highlight.js theme for syntax highlighting (supports light and dark modes)
import "./styles/highlight-theme.css";

// Import main styles last to ensure they override everything else
import "./style.css";

const app = createApp(App);
const pinia = createPinia();

initializeBrowserTimezone();

// Create router
const router = createRouter({
  history: createWebHistory(),
  routes,
});

app.use(pinia);
app.use(router);

// Initialize auth before mounting
import { useAuthStore } from "./stores/auth";
const authStore = useAuthStore();

// Restore auth state, then mount app
authStore.init().finally(() => {
  app.mount("#app");
});
