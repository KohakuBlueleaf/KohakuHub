import { beforeEach, describe, expect, it, vi } from "vitest";

const authApiMock = {
  login: vi.fn(),
  register: vi.fn(),
  logout: vi.fn(),
  me: vi.fn(),
  listExternalTokens: vi.fn(),
};

const settingsApiMock = {
  whoamiV2: vi.fn(),
};

vi.mock("@/utils/api", () => ({
  authAPI: authApiMock,
  settingsAPI: settingsApiMock,
}));

const clearRepoSortPreferenceMock = vi.fn();
vi.mock("@/utils/repoSortPreference", () => ({
  clearRepoSortPreference: clearRepoSortPreferenceMock,
}));

describe("auth store", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    localStorage.clear();
  });

  async function createStore() {
    const piniaModule = await import("pinia");
    piniaModule.setActivePinia(piniaModule.createPinia());
    const authModule = await import("@/stores/auth");
    return authModule.useAuthStore();
  }

  it("loads user info and organizations from whoami", async () => {
    settingsApiMock.whoamiV2.mockResolvedValue({
      data: {
        id: "1",
        name: "owner",
        email: "owner@example.com",
        emailVerified: true,
        orgs: [{ name: "acme-labs" }],
      },
    });
    authApiMock.listExternalTokens.mockResolvedValue({ data: [] });

    const store = await createStore();

    const payload = await store.fetchUserInfo();

    expect(payload.name).toBe("owner");
    expect(store.user).toEqual({
      username: "owner",
      email: "owner@example.com",
      email_verified: true,
      id: "1",
    });
    expect(store.organizationNames).toEqual(["acme-labs"]);
  });

  it("clears auth state when init fails", async () => {
    localStorage.setItem("hf_token", "persisted-token");
    settingsApiMock.whoamiV2.mockRejectedValue(new Error("unauthorized"));

    const store = await createStore();

    await store.init();

    expect(store.user).toBeNull();
    expect(store.token).toBeNull();
    expect(store.externalTokens).toEqual([]);
    expect(localStorage.getItem("hf_token")).toBeNull();
    expect(clearRepoSortPreferenceMock).toHaveBeenCalled();
  });

  it("persists token and checks namespace write permission", async () => {
    settingsApiMock.whoamiV2.mockResolvedValue({
      data: {
        id: "1",
        name: "owner",
        email: "owner@example.com",
        emailVerified: true,
        orgs: [{ name: "acme-labs" }],
      },
    });

    const store = await createStore();

    await store.setToken("api-token");

    expect(localStorage.getItem("hf_token")).toBe("api-token");
    expect(store.canWriteToNamespace("owner")).toBe(true);
    expect(store.canWriteToNamespace("acme-labs")).toBe(true);
    expect(store.canWriteToNamespace("someone-else")).toBe(false);
  });
});
