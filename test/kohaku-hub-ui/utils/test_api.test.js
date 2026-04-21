import { beforeEach, describe, expect, it, vi } from "vitest";

describe("frontend API client", () => {
  async function loadModules() {
    vi.resetModules();
    const apiModule = await import("@/utils/api");
    const lfsModule = await import("@/utils/lfs.js");
    return {
      ...apiModule,
      apiClient: apiModule.default,
      lfsModule,
    };
  }

  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("applies request and response interceptors using stored tokens", async () => {
    localStorage.setItem("hf_token", "local-token");
    localStorage.setItem(
      "hf_external_tokens",
      JSON.stringify([{ url: "https://hf.example", token: "ext-token" }]),
    );

    const { apiClient } = await loadModules();

    const requestHandler = apiClient.interceptors.request.handlers[0];
    const responseHandler = apiClient.interceptors.response.handlers[0];

    const config = requestHandler.fulfilled({ headers: {} });
    expect(config.headers.Authorization).toBe(
      "Bearer local-token|https://hf.example,ext-token",
    );

    localStorage.removeItem("hf_token");
    localStorage.removeItem("hf_external_tokens");
    const emptyConfig = requestHandler.fulfilled({ headers: {} });
    expect(emptyConfig.headers.Authorization).toBeUndefined();

    const payload = { ok: true };
    expect(responseHandler.fulfilled(payload)).toBe(payload);
    await expect(responseHandler.rejected(new Error("boom"))).rejects.toThrow(
      "boom",
    );
  });

  it("routes auth, repo, org, settings, and validation helpers through the shared axios client", async () => {
    const {
      apiClient,
      authAPI,
      repoAPI,
      orgAPI,
      settingsAPI,
      validationAPI,
    } = await loadModules();

    const getSpy = vi.spyOn(apiClient, "get").mockResolvedValue({ data: {} });
    const postSpy = vi.spyOn(apiClient, "post").mockResolvedValue({ data: {} });
    const putSpy = vi.spyOn(apiClient, "put").mockResolvedValue({ data: {} });
    const deleteSpy = vi
      .spyOn(apiClient, "delete")
      .mockResolvedValue({ data: {} });

    await authAPI.register({
      username: "alice",
      email: "alice@example.com",
      password: "secret",
      invitation_token: "invite-token",
    });
    await authAPI.login({ username: "alice", password: "secret" });
    await authAPI.logout();
    await authAPI.me();
    await authAPI.createToken({ name: "ci" });
    await authAPI.listTokens();
    await authAPI.revokeToken("token-id");
    await authAPI.getAvailableSources();
    await authAPI.listExternalTokens("alice");
    await authAPI.addExternalToken("alice", "https://hf.example", "token");
    await authAPI.deleteExternalToken("alice", "https://hf.example");
    await authAPI.bulkUpdateExternalTokens("alice", [
      { url: "https://hf.example", token: "token" },
    ]);

    await repoAPI.create({
      type: "model",
      name: "demo",
      organization: null,
      private: false,
    });
    await repoAPI.delete({
      type: "model",
      name: "demo",
      organization: "acme",
    });
    await repoAPI.getInfo("model", "alice", "demo");
    await repoAPI.listRepos("dataset", { limit: 5, sort: "likes" });
    await repoAPI.getUserOverview("alice", "recent", 10);
    await repoAPI.listTree("model", "alice", "demo", "main", "/nested", {
      recursive: true,
    });
    await repoAPI.listCommits("space", "alice", "demo", "main", {
      limit: 20,
    });

    await orgAPI.create({ name: "acme" });
    await orgAPI.get("acme");
    await orgAPI.addMember("acme", { username: "bob", role: "member" });
    await orgAPI.removeMember("acme", "bob");
    await orgAPI.updateMemberRole("acme", "bob", { role: "admin" });
    await orgAPI.getUserOrgs("alice");
    await orgAPI.updateSettings("acme", { description: "new" });
    await orgAPI.listMembers("acme");

    await settingsAPI.whoamiV2();
    await settingsAPI.getUserProfile("alice");
    await settingsAPI.updateUserSettings("alice", { bio: "hello" });
    await settingsAPI.getOrgProfile("acme");
    await settingsAPI.uploadUserAvatar(
      "alice",
      new File(["avatar"], "avatar.png", { type: "image/png" }),
    );
    await settingsAPI.deleteUserAvatar("alice");
    await settingsAPI.uploadOrgAvatar(
      "acme",
      new File(["avatar"], "avatar.png", { type: "image/png" }),
    );
    await settingsAPI.deleteOrgAvatar("acme");
    await settingsAPI.updateRepoSettings("model", "alice", "demo", {
      private: true,
    });
    await settingsAPI.getLfsSettings("dataset", "alice", "demo");
    await settingsAPI.moveRepo({
      fromRepo: "alice/old",
      toRepo: "alice/new",
      type: "model",
    });
    await settingsAPI.squashRepo({ repo: "alice/demo", type: "model" });
    await settingsAPI.createBranch("model", "alice", "demo", {
      branch: "dev",
    });
    await settingsAPI.deleteBranch("model", "alice", "demo", "dev");
    await settingsAPI.createTag("model", "alice", "demo", {
      tag: "v1.0.0",
      revision: "main",
    });
    await settingsAPI.deleteTag("model", "alice", "demo", "v1.0.0");

    await validationAPI.checkName({
      name: "demo",
      namespace: "alice",
      type: "model",
    });

    expect(postSpy).toHaveBeenCalledWith(
      "/api/auth/register",
      {
        username: "alice",
        email: "alice@example.com",
        password: "secret",
      },
      { params: { invitation_token: "invite-token" } },
    );
    expect(postSpy).toHaveBeenCalledWith("/api/auth/login", {
      username: "alice",
      password: "secret",
    });
    expect(postSpy).toHaveBeenCalledWith("/api/repos/create", {
      type: "model",
      name: "demo",
      organization: null,
      private: false,
    });
    expect(deleteSpy).toHaveBeenCalledWith("/api/repos/delete", {
      data: { type: "model", name: "demo", organization: "acme" },
    });
    expect(getSpy).toHaveBeenCalledWith("/api/models/alice/demo");
    expect(getSpy).toHaveBeenCalledWith("/api/datasets", {
      params: { limit: 5, sort: "likes" },
    });
    expect(getSpy).toHaveBeenCalledWith("/api/users/alice/repos", {
      params: { limit: 10, sort: "recent" },
    });
    expect(getSpy).toHaveBeenCalledWith(
      "/api/models/alice/demo/tree/main/nested",
      { params: { recursive: true } },
    );
    expect(postSpy).toHaveBeenCalledWith("/org/create", { name: "acme" });
    expect(putSpy).toHaveBeenCalledWith(
      "/api/organizations/acme/settings",
      { description: "new" },
    );
    expect(postSpy).toHaveBeenCalledWith(
      "/api/users/alice/avatar",
      expect.any(FormData),
      { headers: { "Content-Type": "multipart/form-data" } },
    );
    expect(postSpy).toHaveBeenCalledWith(
      "/api/organizations/acme/avatar",
      expect.any(FormData),
      { headers: { "Content-Type": "multipart/form-data" } },
    );
    expect(postSpy).toHaveBeenCalledWith("/api/repos/move", {
      fromRepo: "alice/old",
      toRepo: "alice/new",
      type: "model",
    });
    expect(postSpy).toHaveBeenCalledWith("/api/models/alice/demo/branch", {
      branch: "dev",
    });
    expect(deleteSpy).toHaveBeenCalledWith(
      "/api/models/alice/demo/branch/dev",
    );
    expect(postSpy).toHaveBeenCalledWith("/api/models/alice/demo/tag", {
      tag: "v1.0.0",
      revision: "main",
    });
    expect(deleteSpy).toHaveBeenCalledWith(
      "/api/models/alice/demo/tag/v1.0.0",
    );
    expect(postSpy).toHaveBeenCalledWith("/api/validate/check-name", {
      name: "demo",
      namespace: "alice",
      type: "model",
    });
  });

  it("routes invitation, quota, likes, and stats helpers through the shared axios client", async () => {
    const { apiClient, invitationAPI, quotaAPI, likesAPI, statsAPI } =
      await loadModules();

    const getSpy = vi.spyOn(apiClient, "get").mockResolvedValue({ data: {} });
    const postSpy = vi.spyOn(apiClient, "post").mockResolvedValue({ data: {} });
    const putSpy = vi.spyOn(apiClient, "put").mockResolvedValue({ data: {} });
    const deleteSpy = vi
      .spyOn(apiClient, "delete")
      .mockResolvedValue({ data: {} });

    await invitationAPI.create("acme", {
      email: "invitee@example.com",
      role: "member",
    });
    await invitationAPI.get("token-1");
    await invitationAPI.accept("token-1");
    await invitationAPI.list("acme");
    await invitationAPI.delete("token-1");

    await quotaAPI.getRepoQuota("model", "alice", "demo");
    await quotaAPI.setRepoQuota("model", "alice", "demo", {
      quota_bytes: 1024,
    });
    await quotaAPI.recalculateRepoStorage("model", "alice", "demo");
    await quotaAPI.getNamespaceRepoStorage("alice");

    await likesAPI.like("model", "alice", "demo");
    await likesAPI.unlike("model", "alice", "demo");
    await likesAPI.checkLiked("model", "alice", "demo");
    await likesAPI.getLikers("model", "alice", "demo", 10);

    await statsAPI.getStats("model", "alice", "demo");
    await statsAPI.getRecentStats("dataset", "alice", "demo", 14);
    await statsAPI.getTrending("space", 30, 5);

    expect(postSpy).toHaveBeenCalledWith("/api/invitations/org/acme/create", {
      email: "invitee@example.com",
      role: "member",
    });
    expect(postSpy).toHaveBeenCalledWith("/api/invitations/token-1/accept");
    expect(deleteSpy).toHaveBeenCalledWith("/api/invitations/token-1");
    expect(getSpy).toHaveBeenCalledWith("/api/quota/repo/model/alice/demo");
    expect(putSpy).toHaveBeenCalledWith(
      "/api/quota/repo/model/alice/demo",
      { quota_bytes: 1024 },
    );
    expect(postSpy).toHaveBeenCalledWith(
      "/api/quota/repo/model/alice/demo/recalculate",
    );
    expect(postSpy).toHaveBeenCalledWith("/api/models/alice/demo/like");
    expect(deleteSpy).toHaveBeenCalledWith("/api/models/alice/demo/like");
    expect(getSpy).toHaveBeenCalledWith("/api/models/alice/demo/likers", {
      params: { limit: 10 },
    });
    expect(getSpy).toHaveBeenCalledWith("/api/models/alice/demo/stats");
    expect(getSpy).toHaveBeenCalledWith(
      "/api/datasets/alice/demo/stats/recent",
      { params: { days: 14 } },
    );
    expect(getSpy).toHaveBeenCalledWith("/api/trending", {
      params: { repo_type: "space", days: 30, limit: 5 },
    });
  });

  it("follows paginated tree Link headers and submits expanded paths-info forms", async () => {
    const { apiClient, repoAPI } = await loadModules();

    const getSpy = vi
      .spyOn(apiClient, "get")
      .mockResolvedValueOnce({
        data: [{ path: "docs" }],
        headers: {
          link: '<https://hub.local/api/models/alice/demo/tree/main/docs?cursor=page-2>; rel="next"',
        },
      })
      .mockResolvedValueOnce({
        data: [{ path: "docs/guide.md" }],
        headers: {},
      });
    const postSpy = vi.spyOn(apiClient, "post").mockResolvedValue({ data: [] });

    const allEntries = await repoAPI.listTreeAll(
      "model",
      "alice",
      "demo",
      "main",
      "/docs",
      { recursive: false },
    );
    await repoAPI.getPathsInfo(
      "model",
      "alice",
      "demo",
      "main",
      ["docs", "docs/guide.md"],
      true,
    );

    expect(allEntries).toEqual([{ path: "docs" }, { path: "docs/guide.md" }]);
    expect(getSpy).toHaveBeenNthCalledWith(
      1,
      "/api/models/alice/demo/tree/main/docs",
      { params: { recursive: false } },
    );
    expect(getSpy).toHaveBeenNthCalledWith(
      2,
      "https://hub.local/api/models/alice/demo/tree/main/docs?cursor=page-2",
    );

    expect(postSpy).toHaveBeenCalledTimes(1);
    expect(postSpy.mock.calls[0][0]).toBe(
      "/api/models/alice/demo/paths-info/main",
    );
    expect(postSpy.mock.calls[0][1]).toBeInstanceOf(URLSearchParams);
    expect(postSpy.mock.calls[0][1].toString()).toBe(
      "paths=docs&paths=docs%2Fguide.md&expand=true",
    );
    expect(postSpy.mock.calls[0][2]).toEqual({
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
      },
    });
  });

  it("builds NDJSON commits for ignored, regular, LFS, and editor flows", async () => {
    const originalFileReader = globalThis.FileReader;
    globalThis.FileReader = class {
      readAsDataURL(blob) {
        const reader = new originalFileReader();
        reader.onload = (event) => {
          this.result = `data:text/plain;base64,${Buffer.from(event.target.result).toString("base64")}`;
          this.onload?.({ target: this });
        };
        reader.readAsArrayBuffer(blob);
      }
    };

    const { apiClient, repoAPI, lfsModule } = await loadModules();

    vi.spyOn(lfsModule, "calculateSHA256")
      .mockResolvedValueOnce("sha-skip")
      .mockResolvedValueOnce("sha-regular")
      .mockResolvedValueOnce("sha-lfs")
      .mockResolvedValueOnce("sha-missing");

    vi.spyOn(lfsModule, "uploadLFSFile").mockImplementation(
      async (repoId, file, sha256, onProgress) => {
        expect(repoId).toBe("alice/demo");
        onProgress(0.5);
        onProgress(1);
        return { oid: `oid-${sha256}`, size: file.size };
      },
    );

    const postSpy = vi
      .spyOn(apiClient, "post")
      .mockResolvedValueOnce({
        data: {
          files: [
            {
              path: "skip.txt",
              shouldIgnore: true,
              uploadMode: "regular",
            },
            {
              path: "notes.md",
              shouldIgnore: false,
              uploadMode: "regular",
            },
            {
              path: "weights/model.bin",
              shouldIgnore: false,
              uploadMode: "lfs",
            },
          ],
        },
      })
      .mockResolvedValueOnce({
        data: {
          commitOid: "abc123",
          commitUrl: "models/alice/demo/commit/abc123",
        },
      });

    const hashProgress = vi.fn();
    const uploadProgress = vi.fn();

    const response = await repoAPI.uploadFiles(
      "model",
      "alice",
      "demo",
      "main",
      {
        message: "Upload files",
        description: "Fixture-driven upload",
        files: [
          {
            path: "skip.txt",
            file: new File(["same"], "skip.txt", { type: "text/plain" }),
          },
          {
            path: "notes.md",
            file: new File(["hello"], "notes.md", { type: "text/plain" }),
          },
          {
            path: "weights/model.bin",
            file: new File(["0101"], "model.bin", {
              type: "application/octet-stream",
            }),
          },
        ],
      },
      {
        onHashProgress: hashProgress,
        onUploadProgress: uploadProgress,
      },
    );

    expect(response.data.commitOid).toBe("abc123");
    expect(postSpy).toHaveBeenNthCalledWith(
      1,
      "/api/models/alice/demo/preupload/main",
      {
        files: [
          { path: "skip.txt", size: 4, sha256: "sha-skip" },
          { path: "notes.md", size: 5, sha256: "sha-regular" },
          { path: "weights/model.bin", size: 4, sha256: "sha-lfs" },
        ],
      },
    );

    const uploadCommitLines = postSpy.mock.calls[1][1]
      .split("\n")
      .map((line) => JSON.parse(line));
    expect(uploadCommitLines).toEqual([
      {
        key: "header",
        value: {
          summary: "Upload files",
          description: "Fixture-driven upload",
        },
      },
      {
        key: "file",
        value: {
          path: "notes.md",
          content: "aGVsbG8=",
          encoding: "base64",
        },
      },
      {
        key: "lfsFile",
        value: {
          path: "weights/model.bin",
          oid: "oid-sha-lfs",
          size: 4,
          algo: "sha256",
        },
      },
    ]);
    expect(uploadProgress).toHaveBeenCalledWith("notes.md", 1);
    expect(uploadProgress).toHaveBeenCalledWith("model.bin", 1);
    expect(hashProgress).toHaveBeenCalledWith("model.bin", 1);

    postSpy.mockReset();
    postSpy.mockResolvedValueOnce({
      data: {
        files: [],
      },
    });

    await expect(
      repoAPI.uploadFiles(
        "dataset",
        "alice",
        "demo",
        "main",
        {
          message: "Upload",
          files: [
            {
              path: "missing.txt",
              file: new File(["x"], "missing.txt", { type: "text/plain" }),
            },
          ],
        },
      ),
    ).rejects.toThrow("No preupload result for missing.txt");

    postSpy.mockReset();
    postSpy.mockResolvedValue({ data: { commitOid: "commit-2" } });

    await repoAPI.commitFiles("model", "alice", "demo", "main", {
      message: "Edit README",
      description: "Apply offline fixture changes",
      files: [
        {
          path: "README.md",
          content: "Hello, 世界",
        },
      ],
      operations: [
        {
          operation: "deletedFile",
          path: "obsolete.txt",
        },
      ],
    });

    const editorCommitLines = postSpy.mock.calls[0][1]
      .split("\n")
      .map((line) => JSON.parse(line));
    expect(editorCommitLines[0]).toEqual({
      key: "header",
      value: {
        summary: "Edit README",
        description: "Apply offline fixture changes",
      },
    });
    expect(editorCommitLines[1].key).toBe("file");
    expect(editorCommitLines[1].value.path).toBe("README.md");
    expect(editorCommitLines[1].value.encoding).toBe("base64");
    expect(editorCommitLines[2]).toEqual({
      key: "deletedFile",
      value: {
        path: "obsolete.txt",
      },
    });

    globalThis.FileReader = originalFileReader;
  });
});
