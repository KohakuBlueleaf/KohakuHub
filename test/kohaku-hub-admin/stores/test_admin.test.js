import { beforeEach, describe, expect, it, vi } from "vitest";

import { http, HttpResponse } from "@/testing/msw";
import { server } from "../setup/msw-server";

describe("admin store", () => {
  const requests = [];

  function installHandlers({ status = 200 } = {}) {
    requests.length = 0;

    server.use(
      http.get("/admin/api/stats", ({ request }) => {
        requests.push(request.headers.get("X-Admin-Token"));

        if (status === 200) {
          return HttpResponse.json({ users: 1 });
        }
        if (status === 401) {
          return HttpResponse.json({ detail: "unauthorized" }, { status: 401 });
        }
        return HttpResponse.json({ detail: "network" }, { status });
      }),
    );
  }

  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    installHandlers();
  });

  async function createStore() {
    const piniaModule = await import("pinia");
    piniaModule.setActivePinia(piniaModule.createPinia());
    const adminModule = await import("@/stores/admin");
    return adminModule.useAdminStore();
  }

  it("stores the token in memory only after successful verification", async () => {
    const store = await createStore();
    const result = await store.login("admin-secret");

    expect(result).toBe(true);
    expect(requests).toEqual(["admin-secret"]);
    expect(store.token).toBe("admin-secret");
    expect(store.isAuthenticated).toBe(true);
    expect(store.hasToken).toBe(true);
    expect(localStorage.length).toBe(0);
  });

  it("clears auth state when token verification fails", async () => {
    installHandlers({ status: 401 });

    const store = await createStore();
    store.token = "old-token";
    store.isAuthenticated = true;

    const result = await store.login("bad-token");

    expect(result).toBe(false);
    expect(requests).toEqual(["bad-token"]);
    expect(store.token).toBe("");
    expect(store.isAuthenticated).toBe(false);
    expect(store.hasToken).toBe(false);
  });

  it("clears auth state on errors and supports logout", async () => {
    installHandlers({ status: 500 });

    const store = await createStore();

    await expect(store.login("token")).rejects.toBeDefined();
    expect(requests).toEqual(["token"]);
    expect(store.token).toBe("");
    expect(store.isAuthenticated).toBe(false);

    store.token = "persisted";
    store.isAuthenticated = true;
    store.logout();

    expect(store.token).toBe("");
    expect(store.isAuthenticated).toBe(false);
  });
});
