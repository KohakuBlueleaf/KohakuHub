import { beforeEach, describe, expect, it, vi } from "vitest";

import { http } from "@/testing/msw";
import {
  cloneFixture,
  jsonResponse,
  readJsonBody,
  uiApiFixtures,
} from "../helpers/api-fixtures";
import { server } from "../setup/msw-server";

const clearRepoSortPreferenceMock = vi.fn();
vi.mock("@/utils/repoSortPreference", () => ({
  clearRepoSortPreference: clearRepoSortPreferenceMock,
}));

describe("auth store", () => {
  const requests = {
    externalTokens: [],
    login: [],
    logout: 0,
    me: 0,
    register: [],
    whoami: 0,
  };

  function installHandlers({
    externalTokensStatus = 200,
    externalTokensResponse = cloneFixture(uiApiFixtures.auth.externalTokens),
    loginStatus = 200,
    loginResponse = cloneFixture(uiApiFixtures.auth.login),
    logoutStatus = 200,
    logoutResponse = {},
    meStatus = 200,
    meResponse = cloneFixture(uiApiFixtures.auth.me),
    registerStatus = 200,
    registerResponse = cloneFixture(uiApiFixtures.auth.register),
    whoamiStatus = 200,
    whoamiResponse = cloneFixture(uiApiFixtures.auth.whoamiV2),
  } = {}) {
    requests.externalTokens.length = 0;
    requests.login.length = 0;
    requests.logout = 0;
    requests.me = 0;
    requests.register.length = 0;
    requests.whoami = 0;

    server.use(
      http.get("/api/whoami-v2", () => {
        requests.whoami += 1;
        return jsonResponse(whoamiResponse, { status: whoamiStatus });
      }),
      http.get("/api/auth/me", () => {
        requests.me += 1;
        return jsonResponse(meResponse, { status: meStatus });
      }),
      http.post("/api/auth/login", async ({ request }) => {
        requests.login.push(await readJsonBody(request));
        return jsonResponse(loginResponse, { status: loginStatus });
      }),
      http.post("/api/auth/register", async ({ request }) => {
        requests.register.push(await readJsonBody(request));
        return jsonResponse(registerResponse, { status: registerStatus });
      }),
      http.post("/api/auth/logout", () => {
        requests.logout += 1;
        return jsonResponse(logoutResponse, { status: logoutStatus });
      }),
      http.get("/api/users/:username/external-tokens", ({ params }) => {
        requests.externalTokens.push(params.username);
        return jsonResponse(externalTokensResponse, {
          status: externalTokensStatus,
        });
      }),
    );
  }

  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    localStorage.clear();
    installHandlers();
  });

  async function createStore() {
    const piniaModule = await import("pinia");
    piniaModule.setActivePinia(piniaModule.createPinia());
    const authModule = await import("@/stores/auth");
    return authModule.useAuthStore();
  }

  it("loads user info and organizations from whoami", async () => {
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
    expect(requests.whoami).toBe(1);
  });

  it("logs in, registers, and loads external tokens through the shared APIs", async () => {
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

    expect(requests.register).toEqual([
      {
        username: "owner",
        email: "owner@example.com",
        password: "secret",
      },
    ]);
    expect(requests.login).toEqual([
      {
        username: "owner",
        password: "secret",
      },
    ]);
    expect(requests.whoami).toBe(1);
    expect(requests.externalTokens).toEqual(["owner"]);
    expect(store.externalTokens).toEqual([
      { url: "https://hf.co", token: "masked" },
    ]);
  });

  it("fetches a user directly and rejects anonymous namespace writes", async () => {
    const store = await createStore();

    const payload = await store.fetchUser();

    expect(payload).toEqual({ username: "owner" });
    expect(store.user).toEqual({ username: "owner" });
    expect(store.canWriteToNamespace("owner")).toBe(true);
    expect(requests.me).toBe(1);

    store.user = null;
    expect(store.canWriteToNamespace("owner")).toBe(false);
  });

  it("defaults missing organization lists to an empty array during init", async () => {
    installHandlers({
      whoamiResponse: {
        id: "1",
        name: "owner",
        email: "owner@example.com",
        emailVerified: true,
      },
      externalTokensResponse: [],
    });

    const store = await createStore();

    await store.init();

    expect(store.userOrganizations).toEqual([]);
    expect(store.initialized).toBe(true);
  });

  it("handles successful logout and empty external token payloads", async () => {
    installHandlers({
      externalTokensResponse: null,
    });

    const store = await createStore();
    store.user = { username: "owner" };
    store.token = "persisted-token";

    await store.loadExternalTokens();
    expect(store.externalTokens).toEqual([]);

    await expect(store.logout()).resolves.toBeUndefined();
    expect(requests.logout).toBe(1);
    expect(store.user).toBeNull();
    expect(store.token).toBeNull();
  });

  it("clears auth state when init fails", async () => {
    localStorage.setItem("hf_token", "persisted-token");
    installHandlers({
      whoamiStatus: 401,
      whoamiResponse: { detail: "unauthorized" },
    });

    const store = await createStore();

    await store.init();

    expect(store.user).toBeNull();
    expect(store.token).toBeNull();
    expect(store.externalTokens).toEqual([]);
    expect(localStorage.getItem("hf_token")).toBeNull();
    expect(clearRepoSortPreferenceMock).toHaveBeenCalled();
  });

  it("persists token and checks namespace write permission", async () => {
    const store = await createStore();

    await store.setToken("api-token");

    expect(localStorage.getItem("hf_token")).toBe("api-token");
    expect(store.canWriteToNamespace("owner")).toBe(true);
    expect(store.canWriteToNamespace("acme-labs")).toBe(true);
    expect(store.canWriteToNamespace("someone-else")).toBe(false);
  });

  it("clears state on fetch failures and skips repeated init calls", async () => {
    installHandlers({
      externalTokensResponse: [],
      meStatus: 401,
      meResponse: { detail: "expired" },
    });

    const store = await createStore();

    await store.init();
    await store.init();

    expect(requests.whoami).toBe(1);
    expect(requests.externalTokens).toEqual(["owner"]);

    await expect(store.fetchUser()).rejects.toBeDefined();
    expect(store.user).toBeNull();
    expect(store.userOrganizations).toEqual([]);
  });

  it("clears local state on logout even when the API errors", async () => {
    installHandlers({
      logoutStatus: 500,
      logoutResponse: { detail: "network" },
    });

    const store = await createStore();
    store.user = { username: "owner" };
    store.userOrganizations = [{ name: "acme-labs" }];
    store.token = "persisted-token";
    store.externalTokens = [{ url: "https://hf.co", token: "masked" }];
    localStorage.setItem("hf_token", "persisted-token");

    await expect(store.logout()).rejects.toBeDefined();

    expect(store.user).toBeNull();
    expect(store.token).toBeNull();
    expect(localStorage.getItem("hf_token")).toBeNull();
    expect(clearRepoSortPreferenceMock).toHaveBeenCalled();
  });

  it("handles external token loading for anonymous and failing requests", async () => {
    const store = await createStore();

    await store.loadExternalTokens();
    expect(requests.externalTokens).toEqual([]);
    expect(store.externalTokens).toEqual([]);

    store.user = { username: "owner" };
    installHandlers({
      externalTokensStatus: 500,
      externalTokensResponse: { detail: "boom" },
    });

    await store.loadExternalTokens();
    expect(store.externalTokens).toEqual([]);
  });
});
