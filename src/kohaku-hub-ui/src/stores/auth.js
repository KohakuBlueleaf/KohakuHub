// src/kohaku-hub-ui/src/stores/auth.js
import { defineStore } from "pinia";
import { authAPI, settingsAPI } from "@/utils/api";
import { clearRepoSortPreference } from "@/utils/repoSortPreference";

export const useAuthStore = defineStore("auth", {
  state: () => ({
    user: null,
    userOrganizations: [],
    token: localStorage.getItem("hf_token") || null,
    externalTokens: [], // Array of {url, token} for external fallback sources
    loading: false,
    initialized: false,
  }),

  getters: {
    isAuthenticated: (state) => !!state.user,
    username: (state) => state.user?.username || null,
    organizations: (state) => state.userOrganizations,
    organizationNames: (state) =>
      state.userOrganizations.map((org) => org.name),
  },

  actions: {
    /**
     * Login user
     * @param {Object} credentials - {username, password}
     */
    async login(credentials) {
      this.loading = true;
      try {
        const { data } = await authAPI.login(credentials);
        // Session cookie is set automatically
        clearRepoSortPreference();
        await this.fetchUserInfo();
        return data;
      } finally {
        this.loading = false;
      }
    },

    /**
     * Register new user
     * @param {Object} userData - {username, email, password}
     */
    async register(userData) {
      this.loading = true;
      try {
        const { data } = await authAPI.register(userData);
        return data;
      } finally {
        this.loading = false;
      }
    },

    /**
     * Logout current user
     */
    async logout() {
      try {
        await authAPI.logout();
      } finally {
        this.user = null;
        this.token = null;
        localStorage.removeItem("hf_token");
        clearRepoSortPreference();
      }
    },

    /**
     * Fetch current user info
     */
    async fetchUser() {
      try {
        const { data } = await authAPI.me();
        this.user = data;
        return data;
      } catch (err) {
        this.user = null;
        this.userOrganizations = [];
        clearRepoSortPreference();
        throw err;
      }
    },

    /**
     * Fetch user info with organizations
     */
    async fetchUserInfo() {
      try {
        const { data } = await settingsAPI.whoamiV2();
        this.user = {
          username: data.name,
          email: data.email,
          email_verified: data.emailVerified,
          id: data.id,
        };
        this.userOrganizations = data.orgs || [];
        return data;
      } catch (err) {
        this.user = null;
        this.userOrganizations = [];
        clearRepoSortPreference();
        throw err;
      }
    },

    /**
     * Set token and fetch user
     * @param {string} token - API token
     */
    async setToken(token) {
      this.token = token;
      localStorage.setItem("hf_token", token);
      clearRepoSortPreference();
      await this.fetchUserInfo();
    },

    /**
     * Load user's external tokens from database
     */
    async loadExternalTokens() {
      if (!this.user) {
        this.externalTokens = [];
        return;
      }

      try {
        const { data } = await authAPI.listExternalTokens(this.user.username);
        // Store decrypted tokens (API returns masked preview, need to fetch full tokens)
        // For now, we'll load them when user edits settings
        this.externalTokens = data || [];
      } catch (err) {
        console.error("Failed to load external tokens:", err);
        this.externalTokens = [];
      }
    },

    /**
     * Initialize auth state (restore session on app load)
     */
    async init() {
      if (this.initialized) return;

      this.initialized = true;

      // Try to restore user from session cookie or token
      try {
        await this.fetchUserInfo();
        await this.loadExternalTokens();
      } catch (err) {
        // Session expired or invalid, clear state
        this.user = null;
        this.userOrganizations = [];
        this.token = null;
        this.externalTokens = [];
        localStorage.removeItem("hf_token");
        clearRepoSortPreference();
      }
    },

    /**
     * Check if user has write access to a namespace
     * @param {string} namespace - Namespace to check
     * @returns {boolean}
     */
    canWriteToNamespace(namespace) {
      if (!this.isAuthenticated) return false;
      // Check if it's user's own namespace or an organization they belong to
      return (
        this.username === namespace ||
        this.organizationNames.includes(namespace)
      );
    },
  },
});
