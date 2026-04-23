import { beforeEach, describe, expect, it, vi } from "vitest";
import axios from "@/testing/axios";

const client = {
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
};

describe("admin API client", () => {
  async function loadModule() {
    vi.resetModules();
    return import("@/utils/api");
  }

  beforeEach(() => {
    vi.restoreAllMocks();

    vi.spyOn(axios, "create").mockReturnValue(client);
    vi.spyOn(axios, "post").mockResolvedValue({ data: {} });
    vi.spyOn(axios, "delete").mockResolvedValue({ data: {} });

    client.get.mockResolvedValue({ data: {} });
    client.post.mockResolvedValue({ data: {} });
    client.put.mockResolvedValue({ data: {} });
    client.patch.mockResolvedValue({ data: {} });
    client.delete.mockResolvedValue({ data: {} });
  });

  it("routes admin endpoints through axios clients with the admin token header", async () => {
    const api = await loadModule();

    await api.listUsers("admin-token", {
      search: "mai",
      limit: 10,
      offset: 5,
      include_orgs: true,
    });
    await api.getUserInfo("admin-token", "mai_lin");
    await api.createUser("admin-token", { username: "new-user" });
    await api.deleteUser("admin-token", "mai_lin", true);
    await api.setEmailVerification("admin-token", "mai_lin", true);
    await api.updateUserQuota("admin-token", "mai_lin", 1000, 2000);

    await api.getQuota("admin-token", "aurora-labs", true);
    await api.setQuota(
      "admin-token",
      "aurora-labs",
      { private_quota_bytes: 1, public_quota_bytes: 2 },
      true,
    );
    await api.recalculateQuota("admin-token", "aurora-labs", true);
    await api.getQuotaOverview("admin-token");

    await api.getSystemStats("admin-token");
    await api.getDetailedStats("admin-token");
    await api.getTimeseriesStats("admin-token", 14);
    await api.getTopRepositories("admin-token", 5, "size");

    await api.listRepositories("admin-token", {
      search: "lineart",
      repo_type: "model",
      namespace: "mai_lin",
      limit: 5,
      offset: 10,
    });
    await api.getRepositoryDetails(
      "admin-token",
      "model",
      "mai_lin",
      "lineart-caption-base",
    );
    await api.getRepositoryFiles(
      "admin-token",
      "model",
      "mai_lin",
      "lineart-caption-base",
      "dev",
    );
    await api.getRepositoryStorageBreakdown(
      "admin-token",
      "model",
      "mai_lin",
      "lineart-caption-base",
    );
    await api.listCommits("admin-token", {
      repo_full_id: "mai_lin/lineart-caption-base",
      username: "mai_lin",
      limit: 20,
      offset: 40,
    });

    await api.listS3Buckets("admin-token");
    await api.listS3Objects("admin-token", "", {
      prefix: "models/",
      limit: 100,
    });
    await api.recalculateAllRepoStorage("admin-token", {
      repo_type: "dataset",
      namespace: "aurora-labs",
    });

    await api.createRegisterInvitation("admin-token", {
      role: "member",
      max_usage: 1,
      expires_days: 7,
    });
    await api.listInvitations("admin-token", {
      action: "register",
      limit: 10,
      offset: 20,
    });
    await api.deleteInvitation("admin-token", "invite-token");

    await api.globalSearch("admin-token", "mai", ["users", "repos"], 8);

    await api.listDatabaseTables("admin-token");
    await api.getDatabaseQueryTemplates("admin-token");
    await api.executeDatabaseQuery("admin-token", "select 1");

    await api.listFallbackSources("admin-token", {
      namespace: "mai_lin",
      enabled: true,
    });
    await api.getFallbackSource("admin-token", 3);
    await api.createFallbackSource("admin-token", {
      name: "Mirror",
      namespace: "",
      url: "https://hf.example",
      enabled: true,
      priority: 10,
      source_type: "huggingface",
    });
    await api.updateFallbackSource("admin-token", 3, {
      enabled: false,
    });
    await api.deleteFallbackSource("admin-token", 3);
    await api.getFallbackCacheStats("admin-token");
    await api.clearFallbackCache("admin-token");

    await api.deleteRepositoryAdmin(
      "admin-token",
      "model",
      "mai_lin",
      "lineart-caption-base",
    );
    await api.moveRepositoryAdmin(
      "admin-token",
      "model",
      "mai_lin",
      "lineart-caption-base",
      "aurora-labs",
      "lineart-caption-pro",
    );
    await api.squashRepositoryAdmin(
      "admin-token",
      "model",
      "mai_lin",
      "lineart-caption-base",
    );

    await api.deleteS3Object("admin-token", "models/demo/file.bin");
    await api.prepareDeleteS3Prefix("admin-token", "models/demo/");
    await api.deleteS3Prefix("admin-token", "models/demo/", "confirm-123");

    expect(axios.create).toHaveBeenCalledWith({
      baseURL: "/admin/api",
      headers: {
        "X-Admin-Token": "admin-token",
      },
    });

    expect(client.get).toHaveBeenCalledWith("/users", {
      params: { search: "mai", limit: 10, offset: 5, include_orgs: true },
    });
    expect(client.patch).toHaveBeenCalledWith(
      "/users/mai_lin/email-verification",
      null,
      { params: { verified: true } },
    );
    expect(client.put).toHaveBeenCalledWith("/users/mai_lin/quota", {
      private_quota_bytes: 1000,
      public_quota_bytes: 2000,
    });
    expect(client.get).toHaveBeenCalledWith("/quota/aurora-labs", {
      params: { is_org: true },
    });
    expect(client.post).toHaveBeenCalledWith(
      "/quota/aurora-labs/recalculate",
      null,
      { params: { is_org: true } },
    );
    expect(client.get).toHaveBeenCalledWith("/stats/timeseries", {
      params: { days: 14 },
    });
    expect(client.get).toHaveBeenCalledWith("/stats/top-repos", {
      params: { limit: 5, by: "size" },
    });
    expect(client.get).toHaveBeenCalledWith("/repositories", {
      params: {
        search: "lineart",
        repo_type: "model",
        namespace: "mai_lin",
        limit: 5,
        offset: 10,
      },
    });
    expect(client.get).toHaveBeenCalledWith(
      "/repositories/model/mai_lin/lineart-caption-base/files",
      { params: { ref: "dev" } },
    );
    expect(client.get).toHaveBeenCalledWith("/storage/objects", {
      params: { prefix: "models/", limit: 100 },
    });
    expect(client.get).toHaveBeenCalledWith("/search", {
      params: { q: "mai", types: ["users", "repos"], limit: 8 },
    });
    expect(client.post).toHaveBeenCalledWith("/database/query", {
      sql: "select 1",
    });
    expect(client.get).toHaveBeenCalledWith("/fallback-sources", {
      params: { namespace: "mai_lin", enabled: true },
    });
    expect(client.delete).toHaveBeenCalledWith(
      "/storage/objects/models%2Fdemo%2Ffile.bin",
    );
    expect(client.post).toHaveBeenCalledWith(
      "/storage/prefix/prepare-delete",
      null,
      { params: { prefix: "models/demo/" } },
    );
    expect(client.delete).toHaveBeenCalledWith("/storage/prefix", {
      params: { prefix: "models/demo/", confirm_token: "confirm-123" },
    });
    expect(axios.delete).toHaveBeenCalledWith("/api/repos/delete", {
      headers: { "X-Admin-Token": "admin-token" },
      data: {
        type: "model",
        name: "lineart-caption-base",
        organization: "mai_lin",
      },
    });
    expect(axios.post).toHaveBeenCalledWith(
      "/api/repos/move",
      {
        fromRepo: "mai_lin/lineart-caption-base",
        toRepo: "aurora-labs/lineart-caption-pro",
        type: "model",
      },
      {
        headers: { "X-Admin-Token": "admin-token" },
      },
    );
    expect(axios.post).toHaveBeenCalledWith(
      "/api/repos/squash",
      {
        repo: "mai_lin/lineart-caption-base",
        type: "model",
      },
      {
        headers: { "X-Admin-Token": "admin-token" },
      },
    );
  });

  it("verifies admin tokens and formats quota sizes safely", async () => {
    const api = await loadModule();

    client.get
      .mockResolvedValueOnce({ data: {} })
      .mockRejectedValueOnce({ response: { status: 401 } })
      .mockRejectedValueOnce(new Error("backend-down"));

    await expect(api.verifyAdminToken("valid-token")).resolves.toBe(true);
    await expect(api.verifyAdminToken("invalid-token")).resolves.toBe(false);
    await expect(api.verifyAdminToken("broken-token")).rejects.toThrow(
      "backend-down",
    );

    expect(api.formatBytes(null)).toBe("Unlimited");
    expect(api.formatBytes(0)).toBe("0 Bytes");
    expect(api.formatBytes(1530, 1)).toBe("1.5 KB");
    expect(api.parseSize("10GB")).toBe(10_000_000_000);
    expect(api.parseSize("1.5 MB")).toBe(1_500_000);
    expect(api.parseSize("unlimited")).toBeNull();
    expect(api.parseSize("broken")).toBeNull();
  });
});
