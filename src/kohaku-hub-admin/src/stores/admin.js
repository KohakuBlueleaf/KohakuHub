import { defineStore, acceptHMRUpdate } from "pinia";
import { ref, computed } from "vue";
import { verifyAdminToken } from "@/utils/api";
import { resetChainTesterState } from "@/composables/useChainTesterState";

/**
 * Admin store - manages admin token in memory only (no persistence)
 */
export const useAdminStore = defineStore("admin", () => {
  // State - token is kept in memory only, not persisted
  const token = ref("");
  const isAuthenticated = ref(false);

  // Computed
  const hasToken = computed(() => !!token.value);

  // Actions
  async function login(adminToken) {
    // Verify token is valid before storing
    try {
      const valid = await verifyAdminToken(adminToken);
      if (valid) {
        token.value = adminToken;
        isAuthenticated.value = true;
        return true;
      } else {
        token.value = "";
        isAuthenticated.value = false;
        return false;
      }
    } catch (error) {
      token.value = "";
      isAuthenticated.value = false;
      throw error;
    }
  }

  function logout() {
    token.value = "";
    isAuthenticated.value = false;
    // Reset module-level chain tester state so the next operator's
    // session starts clean (the state survives SPA route switches
    // intentionally — see useChainTesterState for the rationale —
    // but a logout should always wipe it).
    resetChainTesterState();
  }

  return {
    token,
    isAuthenticated,
    hasToken,
    login,
    logout,
  };
});

// Hot-replace this store on edit instead of full-reloading the page.
// Without this, editing admin.js would full-reload and dump the in-memory
// token, making local development of auth-related code painful.
if (import.meta.hot) {
  import.meta.hot.accept(acceptHMRUpdate(useAdminStore, import.meta.hot));
}
