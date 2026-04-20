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

  it("logs in, registers, and loads external tokens through the shared APIs", async () => {
    authApiMock.login.mockResolvedValue({ data: { ok: true } });
    authApiMock.register.mockResolvedValue({
      data: { message: "Registration successful" },
    });
    authApiMock.listExternalTokens.mockResolvedValue({
      data: [{ url: "https://hf.co", token: "masked" }],
    });
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

    await expect(
      store.register({
        username: "owner",
        email: "owner@example.com",
        password: "secret",
      }),
    ).resolves.toEqual({
      message: "Registration successful",
    });

    await expect(
      store.login({
        username: "owner",
        password: "secret",
      }),
    ).resolves.toEqual({ ok: true });
    await store.loadExternalTokens();

    expect(authApiMock.login).toHaveBeenCalledWith({
      username: "owner",
      password: "secret",
    });
    expect(authApiMock.register).toHaveBeenCalledWith({
      username: "owner",
      email: "owner@example.com",
      password: "secret",
    });
    expect(store.externalTokens).toEqual([
      { url: "https://hf.co", token: "masked" },
    ]);
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

  it("clears state on fetch failures and skips repeated init calls", async () => {
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
    authApiMock.me.mockRejectedValue(new Error("expired"));

    const store = await createStore();

    await store.init();
    await store.init();

    expect(settingsApiMock.whoamiV2).toHaveBeenCalledTimes(1);
    expect(authApiMock.listExternalTokens).toHaveBeenCalledTimes(1);

    await expect(store.fetchUser()).rejects.toThrow("expired");
    expect(store.user).toBeNull();
    expect(store.userOrganizations).toEqual([]);
  });

  it("clears local state on logout even when the API errors", async () => {
    authApiMock.logout.mockRejectedValue(new Error("network"));

    const store = await createStore();
    store.user = { username: "owner" };
    store.userOrganizations = [{ name: "acme-labs" }];
    store.token = "persisted-token";
    store.externalTokens = [{ url: "https://hf.co", token: "masked" }];
    localStorage.setItem("hf_token", "persisted-token");

    await expect(store.logout()).rejects.toThrow("network");

    expect(store.user).toBeNull();
    expect(store.token).toBeNull();
    expect(localStorage.getItem("hf_token")).toBeNull();
    expect(clearRepoSortPreferenceMock).toHaveBeenCalled();
  });

  it("handles external token loading for anonymous and failing requests", async () => {
    const store = await createStore();

    await store.loadExternalTokens();
    expect(authApiMock.listExternalTokens).not.toHaveBeenCalled();
    expect(store.externalTokens).toEqual([]);

    store.user = { username: "owner" };
    authApiMock.listExternalTokens.mockRejectedValue(new Error("boom"));

    await store.loadExternalTokens();
    expect(store.externalTokens).toEqual([]);
  });
});
