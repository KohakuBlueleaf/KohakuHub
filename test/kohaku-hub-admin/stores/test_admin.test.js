import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  verifyAdminToken: vi.fn(),
}));

vi.mock("@/utils/api", () => ({
  verifyAdminToken: apiMocks.verifyAdminToken,
}));

describe("admin store", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
  });

  async function createStore() {
    const piniaModule = await import("pinia");
    piniaModule.setActivePinia(piniaModule.createPinia());
    const adminModule = await import("@/stores/admin");
    return adminModule.useAdminStore();
  }

  it("stores the token in memory only after successful verification", async () => {
    apiMocks.verifyAdminToken.mockResolvedValue(true);

    const store = await createStore();
    const result = await store.login("admin-secret");

    expect(result).toBe(true);
    expect(apiMocks.verifyAdminToken).toHaveBeenCalledWith("admin-secret");
    expect(store.token).toBe("admin-secret");
    expect(store.isAuthenticated).toBe(true);
    expect(store.hasToken).toBe(true);
    expect(localStorage.length).toBe(0);
  });

  it("clears auth state when token verification fails", async () => {
    apiMocks.verifyAdminToken.mockResolvedValue(false);

    const store = await createStore();
    store.token = "old-token";
    store.isAuthenticated = true;

    const result = await store.login("bad-token");

    expect(result).toBe(false);
    expect(store.token).toBe("");
    expect(store.isAuthenticated).toBe(false);
    expect(store.hasToken).toBe(false);
  });

  it("clears auth state on errors and supports logout", async () => {
    apiMocks.verifyAdminToken.mockRejectedValue(new Error("network"));

    const store = await createStore();

    await expect(store.login("token")).rejects.toThrow("network");
    expect(store.token).toBe("");
    expect(store.isAuthenticated).toBe(false);

    store.token = "persisted";
    store.isAuthenticated = true;
    store.logout();

    expect(store.token).toBe("");
    expect(store.isAuthenticated).toBe(false);
  });
});
