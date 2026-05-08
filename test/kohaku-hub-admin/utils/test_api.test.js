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

    await api.getDependencyHealth("admin-token");
    await api.getDependencyHealth("admin-token", { timeoutSeconds: 1.5 });

    await api.listAdminSessions("admin-token");
    await api.listAdminSessions("admin-token", {
      user: "outsider",
      activeOnly: true,
      createdAfter: "2026-01-01T00:00:00Z",
      limit: 5,
      offset: 10,
    });
    await api.revokeAdminSession("admin-token", 42);
    await api.revokeAdminSessionsBulk("admin-token", { user: "outsider" });

    await api.listAdminTokens("admin-token", {
      user: "owner",
      unusedForDays: 30,
    });
    await api.revokeAdminToken("admin-token", 7);

    await api.listAdminSshKeys("admin-token", {
      user: "owner",
      unusedForDays: 90,
    });
    await api.revokeAdminSshKey("admin-token", 3);

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
    await api.invalidateFallbackRepoCache(
      "admin-token",
      "model",
      "mai_lin",
      "lineart-caption-base",
    );
    await api.invalidateFallbackUserCacheById("admin-token", 42);
    await api.invalidateFallbackUserCacheByUsername("admin-token", "mai_lin");
    await api.bulkReplaceFallbackSources("admin-token", [
      {
        namespace: "",
        url: "https://hf.example",
        token: null,
        priority: 10,
        name: "HF",
        source_type: "huggingface",
        enabled: true,
      },
    ]);
    await api.runFallbackChainSimulate("admin-token", {
      op: "info",
      repo_type: "model",
      namespace: "owner",
      name: "demo",
      revision: "main",
      sources: [
        {
          name: "HF", url: "https://hf.example",
          source_type: "huggingface", token: null, priority: 10,
        },
      ],
      as_username: "mai_lin",
      header_tokens: { "https://hf.example": "hf_xxx" },
    });

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
    expect(client.get).toHaveBeenCalledWith("/health/dependencies", {
      params: {},
    });
    expect(client.get).toHaveBeenCalledWith("/health/dependencies", {
      params: { timeout_seconds: 1.5 },
    });
    expect(client.get).toHaveBeenCalledWith("/sessions", {
      params: { limit: 100, offset: 0 },
    });
    expect(client.get).toHaveBeenCalledWith("/sessions", {
      params: {
        limit: 5,
        offset: 10,
        user: "outsider",
        active_only: true,
        created_after: "2026-01-01T00:00:00Z",
      },
    });
    expect(client.delete).toHaveBeenCalledWith("/sessions/42");
    expect(client.post).toHaveBeenCalledWith("/sessions/revoke-bulk", {
      user: "outsider",
    });
    expect(client.get).toHaveBeenCalledWith("/tokens", {
      params: { limit: 100, offset: 0, user: "owner", unused_for_days: 30 },
    });
    expect(client.delete).toHaveBeenCalledWith("/tokens/7");
    expect(client.get).toHaveBeenCalledWith("/ssh-keys", {
      params: { limit: 100, offset: 0, user: "owner", unused_for_days: 90 },
    });
    expect(client.delete).toHaveBeenCalledWith("/ssh-keys/3");
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
      "/fallback-sources/cache/repo/model/mai_lin/lineart-caption-base",
    );
    expect(client.delete).toHaveBeenCalledWith(
      "/fallback-sources/cache/user/42",
    );
    expect(client.delete).toHaveBeenCalledWith(
      "/fallback-sources/cache/username/mai_lin",
    );
    expect(client.put).toHaveBeenCalledWith(
      "/fallback/sources-bulk-replace",
      expect.objectContaining({
        sources: expect.any(Array),
      }),
    );
    expect(client.post).toHaveBeenCalledWith(
      "/fallback/test/simulate",
      expect.objectContaining({
        op: "info",
        as_username: "mai_lin",
        sources: expect.any(Array),
        header_tokens: { "https://hf.example": "hf_xxx" },
      }),
    );
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

  // ---------------------------------------------------------------------
  // Chain-tester helpers (#78 redesign):
  //   - decodeChainTraceHeader: tolerant base64-JSON decoder
  //   - buildProbeRequestTarget: URL + method per op
  //   - runFallbackProbe: real-request driver, parses X-Chain-Trace
  // ---------------------------------------------------------------------

  it("decodeChainTraceHeader decodes valid headers and tolerates broken input", async () => {
    const api = await loadModule();
    const hops = [
      { kind: "local", source_name: "local", decision: "LOCAL_HIT" },
      { kind: "fallback", source_name: "HF", decision: "BIND_AND_RESPOND" },
    ];
    const encoded = btoa(JSON.stringify({ version: 1, hops }));
    expect(api.decodeChainTraceHeader(encoded)).toEqual(hops);

    // Tolerant fallthroughs.
    expect(api.decodeChainTraceHeader(null)).toEqual([]);
    expect(api.decodeChainTraceHeader(undefined)).toEqual([]);
    expect(api.decodeChainTraceHeader("")).toEqual([]);
    expect(api.decodeChainTraceHeader("not-base64")).toEqual([]);
    // Valid base64, invalid JSON.
    expect(api.decodeChainTraceHeader(btoa("{not json"))).toEqual([]);
    // Valid JSON without hops.
    expect(
      api.decodeChainTraceHeader(btoa(JSON.stringify({ version: 1 }))),
    ).toEqual([]);
  });

  it("buildProbeRequestTarget assembles the URL + method for each op", async () => {
    const api = await loadModule();
    const base = { repo_type: "model", namespace: "owner", name: "demo" };

    expect(api.buildProbeRequestTarget({ op: "info", ...base })).toEqual({
      url: "/api/models/owner/demo",
      method: "get",
    });
    expect(
      api.buildProbeRequestTarget({ op: "tree", revision: "main", file_path: "", ...base }),
    ).toEqual({
      url: "/api/models/owner/demo/tree/main",
      method: "get",
    });
    expect(
      api.buildProbeRequestTarget({
        op: "tree",
        revision: "dev",
        file_path: "subdir/leaf",
        ...base,
      }),
    ).toEqual({
      url: "/api/models/owner/demo/tree/dev/subdir/leaf",
      method: "get",
    });
    expect(
      api.buildProbeRequestTarget({
        op: "resolve",
        revision: "main",
        file_path: "config.json",
        ...base,
      }),
    ).toEqual({
      url: "/models/owner/demo/resolve/main/config.json",
      method: "head",
    });
    expect(
      api.buildProbeRequestTarget({ op: "paths_info", revision: "main", ...base }),
    ).toEqual({
      url: "/api/models/owner/demo/paths-info/main",
      method: "post",
    });
    // Bad op throws.
    expect(() =>
      api.buildProbeRequestTarget({ op: "totally-bogus", ...base }),
    ).toThrow(/Unknown probe op/);
  });

  // ``runFallbackProbe`` was refactored from axios to native ``fetch``
  // with ``redirect: 'manual'`` (#78 v3) so cross-origin redirects on
  // resolve fallback don't silently hang in the browser. Tests now
  // mock ``globalThis.fetch`` instead. Helper builds Response-like
  // objects with proper Headers + ``text()`` so the curated-headers
  // and body-preview paths in ``runFallbackProbe`` exercise the same
  // shape they'd see in production.
  function _makeFetchResponse({
    status = 200,
    type = "basic",
    headers = {},
    bodyText = "",
  } = {}) {
    const h = new Headers();
    for (const [k, v] of Object.entries(headers)) h.set(k, v);
    return {
      status,
      type,
      headers: h,
      text: async () => bodyText,
    };
  }

  it("runFallbackProbe sends the real fetch request, decodes X-Chain-Trace, and assembles a report", async () => {
    const api = await loadModule();

    const hops = [
      {
        kind: "local",
        source_name: "local",
        source_url: null,
        source_type: null,
        method: "GET",
        upstream_path: "/api/models/owner/demo",
        status_code: 200,
        x_error_code: null,
        x_error_message: null,
        decision: "LOCAL_HIT",
        duration_ms: 12,
        error: null,
      },
    ];
    const encoded = btoa(JSON.stringify({ version: 1, hops }));

    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        _makeFetchResponse({
          status: 200,
          headers: {
            "content-type": "application/json",
            "x-chain-trace": encoded,
          },
          bodyText: '{"id": "owner/demo", "private": false}',
        }),
      );

    const report = await api.runFallbackProbe({
      op: "info",
      repo_type: "model",
      namespace: "owner",
      name: "demo",
      revision: "main",
      authorization: "Bearer khub_xxx|https://hf.example,hf_yyy",
    });

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe("/api/models/owner/demo");
    expect(init.method).toBe("GET");
    expect(init.headers.Authorization).toBe(
      "Bearer khub_xxx|https://hf.example,hf_yyy",
    );
    // ``redirect: 'manual'`` is non-negotiable — without it, axios's
    // (and fetch's default ``follow``) cross-origin redirect chain
    // silently hangs in the browser on resolve fallback.
    expect(init.redirect).toBe("manual");

    expect(report.final_outcome).toBe("LOCAL_HIT");
    expect(report.bound_source).toEqual({ name: "local", url: null });
    expect(report.attempts).toHaveLength(1);
    expect(report.attempts[0].decision).toBe("LOCAL_HIT");
    expect(report.attempts[0].kind).toBe("local");
    expect(report.final_response.status_code).toBe(200);
    expect(report.final_response.body_preview).toContain('"id": "owner/demo"');
    expect(report.final_response.headers["content-type"]).toBe(
      "application/json",
    );
  });

  it("runFallbackProbe with no Authorization sends no Authorization header", async () => {
    const api = await loadModule();
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(_makeFetchResponse({ bodyText: "{}" }));
    await api.runFallbackProbe({
      op: "info",
      repo_type: "model",
      namespace: "owner",
      name: "demo",
    });
    const [, init] = fetchSpy.mock.calls[0];
    expect(init.headers.Authorization).toBeUndefined();
  });

  it("runFallbackProbe synthesizes a NETWORK_ERROR report when the fetch throws", async () => {
    const api = await loadModule();
    vi.spyOn(globalThis, "fetch").mockRejectedValue(
      new Error("connection refused"),
    );

    const report = await api.runFallbackProbe({
      op: "info",
      repo_type: "model",
      namespace: "owner",
      name: "demo",
    });

    expect(report.final_outcome).toBe("ERROR");
    expect(report.bound_source).toBeNull();
    expect(report.attempts).toHaveLength(1);
    expect(report.attempts[0].decision).toBe("NETWORK_ERROR");
    expect(report.attempts[0].error).toBe("connection refused");
    expect(report.final_response).toBeNull();
  });

  it("runFallbackProbe missing X-Chain-Trace yields an empty timeline (CHAIN_EXHAUSTED)", async () => {
    const api = await loadModule();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      _makeFetchResponse({
        status: 200,
        headers: { "content-type": "text/plain" },
        bodyText: "hello",
      }),
    );
    const report = await api.runFallbackProbe({
      op: "info",
      repo_type: "model",
      namespace: "owner",
      name: "demo",
    });
    expect(report.attempts).toEqual([]);
    expect(report.final_outcome).toBe("CHAIN_EXHAUSTED");
    expect(report.final_response.status_code).toBe(200);
    expect(report.final_response.body_preview).toBe("hello");
  });

  it("runFallbackProbe opaqueredirect path reads cookie + reconstructs final_response from bound hop", async () => {
    // The 3xx case the v3 fix targets: backend returns 307 +
    // ``Location: <cross-origin>`` + Set-Cookie. ``fetch`` with
    // ``redirect: 'manual'`` resolves immediately with type=opaqueredirect
    // (status=0, headers list empty per Fetch spec), and we read the
    // trace off the cookie that was set on the same-origin response.
    _clearAllTraceCookies();
    const api = await loadModule();
    const hops = [
      { kind: "local", source_name: "local", decision: "LOCAL_MISS",
        status_code: 404, duration_ms: 1 },
      { kind: "fallback", source_name: "HF",
        source_url: "https://huggingface.co",
        decision: "BIND_AND_RESPOND",
        status_code: 307, duration_ms: 800 },
    ];
    const encoded = btoa(JSON.stringify({ version: 1, hops }));

    let plantedProbeId = null;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (url, init) => {
      plantedProbeId = init.headers["X-Khub-Probe-Id"];
      // Plant the cookie BEFORE returning the opaqueredirect — same
      // ordering production has (Set-Cookie + Location on the 307).
      document.cookie =
        `_khub_chain_trace_${plantedProbeId}=${encoded}; ` +
        `Max-Age=300; Path=/; SameSite=Lax`;
      return _makeFetchResponse({
        status: 0,
        type: "opaqueredirect",
        headers: {},
        bodyText: "",
      });
    });

    const report = await api.runFallbackProbe({
      op: "resolve", repo_type: "model", namespace: "openai-community",
      name: "gpt2", revision: "main", file_path: "config.json",
    });
    expect(report.attempts).toHaveLength(2);
    expect(report.attempts[1].decision).toBe("BIND_AND_RESPOND");
    expect(report.final_outcome).toBe("BIND_AND_RESPOND");
    // final_response reconstructed from the bound hop, not the
    // (zeroed) opaqueredirect Response.
    expect(report.final_response.status_code).toBe(307);
    expect(report.final_response.body_preview).toContain("redirect");
  });

  it("runFallbackProbe paths_info packs paths into the request body as JSON", async () => {
    const api = await loadModule();
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(_makeFetchResponse({ bodyText: "[]" }));
    await api.runFallbackProbe({
      op: "paths_info",
      repo_type: "dataset",
      namespace: "owner",
      name: "demo",
      revision: "main",
      paths: ["README.md", "config.json"],
    });
    const [url, init] = fetchSpy.mock.calls[0];
    expect(init.method).toBe("POST");
    expect(url).toBe("/api/datasets/owner/demo/paths-info/main");
    // Fetch needs a serialized body — verify the JSON shape.
    expect(JSON.parse(init.body)).toEqual({
      paths: ["README.md", "config.json"],
    });
    expect(init.headers["Content-Type"]).toBe("application/json");
  });

  // -------------------------------------------------------------------
  // Per-probe trace cookie pickup (#78 v3): on redirect-follow paths
  // the W3C Fetch spec strips X-Chain-Trace from JS visibility, so
  // ``runFallbackProbe`` falls back to a per-probe cookie set by the
  // backend. The cookie name is ``_khub_chain_trace_<probeId>`` where
  // probeId comes from the request's ``X-Khub-Probe-Id`` header.
  // -------------------------------------------------------------------

  // Helper: clear any leftover trace cookies between tests so cookie
  // jar from prior cases doesn't leak into the current one. The test
  // file's ``afterEach`` only resets axios mocks.
  function _clearAllTraceCookies() {
    document.cookie
      .split(";")
      .map((c) => c.trim())
      .forEach((cookie) => {
        const eq = cookie.indexOf("=");
        const name = eq > 0 ? cookie.slice(0, eq) : cookie;
        if (name.startsWith("_khub_chain_trace_")) {
          document.cookie = `${name}=; Max-Age=0; Path=/; SameSite=Lax`;
        }
      });
  }

  it("runFallbackProbe sends X-Khub-Probe-Id header on every call", async () => {
    const api = await loadModule();
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(_makeFetchResponse({ bodyText: "{}" }));
    await api.runFallbackProbe({
      op: "info", repo_type: "model", namespace: "owner", name: "demo",
    });
    const [, init] = fetchSpy.mock.calls[0];
    expect(init.headers["X-Khub-Probe-Id"]).toMatch(/.+/);
  });

  it("runFallbackProbe generates a fresh probe id per call", async () => {
    const api = await loadModule();
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(_makeFetchResponse({ bodyText: "{}" }));
    await api.runFallbackProbe({
      op: "info", repo_type: "model", namespace: "owner", name: "demo",
    });
    await api.runFallbackProbe({
      op: "info", repo_type: "model", namespace: "owner", name: "demo",
    });
    const id1 = fetchSpy.mock.calls[0][1].headers["X-Khub-Probe-Id"];
    const id2 = fetchSpy.mock.calls[1][1].headers["X-Khub-Probe-Id"];
    expect(id1).not.toBe(id2);
  });

  it("runFallbackProbe falls back to per-probe cookie when X-Chain-Trace header is missing", async () => {
    _clearAllTraceCookies();
    const api = await loadModule();
    let capturedProbeId = null;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (url, init) => {
      capturedProbeId = init.headers["X-Khub-Probe-Id"];
      const hops = [
        { kind: "local", source_name: "local", decision: "LOCAL_MISS",
          status_code: 404, duration_ms: 1 },
        { kind: "fallback", source_name: "HF", decision: "BIND_AND_RESPOND",
          status_code: 307, duration_ms: 12 },
      ];
      const traceValue = btoa(JSON.stringify({ version: 1, hops }));
      document.cookie =
        `_khub_chain_trace_${capturedProbeId}=${traceValue}; ` +
        `Max-Age=300; Path=/; SameSite=Lax`;
      // Returned without X-Chain-Trace header — forces cookie pickup.
      return _makeFetchResponse({ bodyText: "{}" });
    });

    const report = await api.runFallbackProbe({
      op: "info", repo_type: "model", namespace: "owner", name: "demo",
    });
    expect(report.attempts).toHaveLength(2);
    expect(report.attempts[0].decision).toBe("LOCAL_MISS");
    expect(report.attempts[1].decision).toBe("BIND_AND_RESPOND");
    expect(report.final_outcome).toBe("BIND_AND_RESPOND");
    expect(document.cookie).not.toContain(
      `_khub_chain_trace_${capturedProbeId}=`,
    );
  });

  it("runFallbackProbe prefers X-Chain-Trace header over cookie when both present", async () => {
    _clearAllTraceCookies();
    const api = await loadModule();
    const headerHops = [
      { kind: "local", source_name: "local", decision: "LOCAL_HIT",
        status_code: 200, duration_ms: 7 },
    ];
    const headerEncoded = btoa(JSON.stringify({ version: 1, hops: headerHops }));
    let plantedProbeId = null;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (url, init) => {
      plantedProbeId = init.headers["X-Khub-Probe-Id"];
      const decoyHops = [
        { kind: "local", source_name: "local", decision: "LOCAL_MISS",
          status_code: 404, duration_ms: 1 },
      ];
      const decoy = btoa(JSON.stringify({ version: 1, hops: decoyHops }));
      document.cookie =
        `_khub_chain_trace_${plantedProbeId}=${decoy}; ` +
        `Max-Age=300; Path=/; SameSite=Lax`;
      return _makeFetchResponse({
        headers: { "x-chain-trace": headerEncoded },
        bodyText: "{}",
      });
    });
    const report = await api.runFallbackProbe({
      op: "info", repo_type: "model", namespace: "owner", name: "demo",
    });
    expect(report.attempts).toHaveLength(1);
    expect(report.attempts[0].decision).toBe("LOCAL_HIT");
    expect(report.final_outcome).toBe("LOCAL_HIT");
    expect(document.cookie).not.toContain(
      `_khub_chain_trace_${plantedProbeId}=`,
    );
  });

  it("runFallbackProbe strips surrounding double quotes when reading the cookie", async () => {
    // Defensive guard for upstreams that re-introduce SimpleCookie-
    // style quoting (we patched our own backend, but a reverse proxy
    // or future runtime might still wrap base64 values).
    _clearAllTraceCookies();
    const api = await loadModule();
    const hops = [
      { kind: "local", source_name: "local", decision: "LOCAL_HIT",
        status_code: 200, duration_ms: 1 },
    ];
    const encoded = btoa(JSON.stringify({ version: 1, hops }));
    let plantedProbeId = null;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (url, init) => {
      plantedProbeId = init.headers["X-Khub-Probe-Id"];
      document.cookie =
        `_khub_chain_trace_${plantedProbeId}="${encoded}"; ` +
        `Max-Age=300; Path=/; SameSite=Lax`;
      return _makeFetchResponse({ bodyText: "{}" });
    });
    const report = await api.runFallbackProbe({
      op: "info", repo_type: "model", namespace: "owner", name: "demo",
    });
    expect(report.attempts).toHaveLength(1);
    expect(report.attempts[0].decision).toBe("LOCAL_HIT");
    expect(report.final_outcome).toBe("LOCAL_HIT");
  });

  it("runFallbackProbe doesn't read other probes' cookies (concurrent isolation)", async () => {
    _clearAllTraceCookies();
    const api = await loadModule();
    const otherHops = [{ kind: "local", source_name: "local",
      decision: "LOCAL_HIT", status_code: 200, duration_ms: 1 }];
    const otherEncoded = btoa(JSON.stringify({ version: 1, hops: otherHops }));
    document.cookie =
      `_khub_chain_trace_other-probe=${otherEncoded}; ` +
      `Max-Age=300; Path=/; SameSite=Lax`;

    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      _makeFetchResponse({ bodyText: "{}" }),
    );
    const report = await api.runFallbackProbe({
      op: "info", repo_type: "model", namespace: "owner", name: "demo",
    });
    expect(report.attempts).toEqual([]);
    expect(report.final_outcome).toBe("CHAIN_EXHAUSTED");
    expect(document.cookie).toContain("_khub_chain_trace_other-probe=");
    _clearAllTraceCookies();
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

  it("getCacheStats and resetCacheMetrics route through the admin client", async () => {
    // Two cache-monitoring helpers exposed for the admin SPA's
    // cache page (post-#74). Plain admin-token-headed wrappers
    // around ``GET /cache/stats`` and ``POST /cache/metrics/reset``;
    // worth pinning so a refactor that renames the endpoints (or
    // accidentally drops the admin-token header) gets caught.
    const api = await loadModule();

    client.get.mockResolvedValueOnce({
      data: { metrics: { hits: 100, misses: 5 }, memory: { used_bytes: 1234 } },
    });
    client.post.mockResolvedValueOnce({ data: { reset: true } });

    const stats = await api.getCacheStats("admin-token");
    expect(stats).toEqual({
      metrics: { hits: 100, misses: 5 },
      memory: { used_bytes: 1234 },
    });
    expect(client.get).toHaveBeenCalledWith("/cache/stats");

    const reset = await api.resetCacheMetrics("admin-token");
    expect(reset).toEqual({ reset: true });
    expect(client.post).toHaveBeenCalledWith("/cache/metrics/reset");
  });
});
