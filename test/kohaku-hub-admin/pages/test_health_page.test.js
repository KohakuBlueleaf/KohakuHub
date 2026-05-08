import { defineComponent, h } from "vue";
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ElementPlusStubs } from "../helpers/vue";

const mocks = vi.hoisted(() => ({
  router: {
    push: vi.fn(),
  },
  adminStore: {
    token: "admin-token",
    logout: vi.fn(),
  },
  api: {
    getDependencyHealth: vi.fn(),
  },
}));

vi.mock("vue-router", () => ({
  useRouter: () => mocks.router,
}));

vi.mock("@/stores/admin", () => ({
  useAdminStore: () => mocks.adminStore,
}));

vi.mock("@/utils/api", () => ({
  getDependencyHealth: (...args) => mocks.api.getDependencyHealth(...args),
}));

vi.mock("@/components/AdminLayout.vue", () => ({
  default: defineComponent({
    name: "AdminLayoutStub",
    setup(_, { slots }) {
      return () =>
        h(
          "div",
          { "data-testid": "admin-layout" },
          slots.default ? slots.default() : [],
        );
    },
  }),
}));

vi.mock("element-plus", async () => {
  const actual = await vi.importActual("element-plus");
  return actual;
});

import HealthPage from "@/pages/health.vue";

// Resolved at runtime via the vi.mock above. ``vi.importActual`` goes
// through Vitest's resolver (which respects the vite alias inherited
// from the admin workspace's vitest.config.js), not Vite's
// static-analysis pass, so element-plus loads from
// ``src/kohaku-hub-admin/node_modules`` even though this test file
// lives outside that workspace. We resolve once and cache for use in
// each test that needs to spy on ``ElMessage``.
async function getElMessage() {
  return (await vi.importActual("element-plus")).ElMessage;
}

const SAMPLE_PAYLOAD = {
  overall_status: "ok",
  checked_at_ms: 1_700_000_000_000,
  elapsed_ms: 47,
  timeout_seconds: 2,
  dependencies: [
    {
      name: "postgres",
      status: "ok",
      latency_ms: 12,
      version: "PostgreSQL 15.5",
      endpoint: "postgresql://hub@127.0.0.1:5432/hub",
      detail: null,
    },
    {
      name: "minio",
      status: "ok",
      latency_ms: 9,
      version: "MinIO",
      endpoint: "http://127.0.0.1:9000",
      detail: null,
    },
    {
      name: "lakefs",
      status: "ok",
      latency_ms: 18,
      version: "1.80.0",
      endpoint: "http://127.0.0.1:8000",
      detail: null,
    },
    {
      // Backend reports the probe as ``redis`` regardless of which
      // protocol-compatible server is on the other end (Valkey, KeyDB,
      // Redis itself); the version field is the only place the actual
      // implementation is surfaced — see probe_redis in
      // src/kohakuhub/api/admin/utils/health.py.
      name: "redis",
      status: "ok",
      latency_ms: 4,
      version: "Valkey 8.0.1",
      endpoint: "redis://127.0.0.1:6379/0",
      detail: null,
    },
    {
      name: "smtp",
      status: "disabled",
      latency_ms: null,
      version: null,
      endpoint: null,
      detail: "SMTP is disabled in configuration",
    },
  ],
};

const noop = () => {};
const noopDirective = {
  mounted: noop,
  updated: noop,
  beforeUnmount: noop,
};

function mountPage() {
  return mount(HealthPage, {
    global: {
      stubs: ElementPlusStubs,
      directives: {
        loading: noopDirective,
      },
    },
  });
}

describe("admin health page", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mocks.router.push.mockReset();
    mocks.adminStore.logout.mockReset();
    mocks.adminStore.token = "admin-token";
    mocks.api.getDependencyHealth.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("loads dependency health on mount and renders one card per dependency", async () => {
    mocks.api.getDependencyHealth.mockResolvedValue(SAMPLE_PAYLOAD);
    const wrapper = mountPage();
    await flushPromises();

    expect(mocks.api.getDependencyHealth).toHaveBeenCalledWith("admin-token");

    const cards = wrapper.findAll('[data-testid^="health-card-"]');
    expect(cards).toHaveLength(SAMPLE_PAYLOAD.dependencies.length);

    const overall = wrapper.get('[data-testid="health-overall"]');
    expect(overall.text()).toContain("OK");

    const postgresCard = wrapper.get('[data-testid="health-card-postgres"]');
    expect(postgresCard.text()).toContain("PostgreSQL");
    expect(postgresCard.text()).toContain("12 ms");
    expect(postgresCard.text()).toContain("PostgreSQL 15.5");
    expect(postgresCard.text()).toContain("postgresql://hub@127.0.0.1:5432/hub");

    const smtpCard = wrapper.get('[data-testid="health-card-smtp"]');
    expect(smtpCard.text()).toContain("Disabled");
    expect(smtpCard.text()).toContain("SMTP is disabled in configuration");
  });

  it("re-fetches when the user clicks Re-check", async () => {
    mocks.api.getDependencyHealth.mockResolvedValue(SAMPLE_PAYLOAD);
    const wrapper = mountPage();
    await flushPromises();

    const button = wrapper.get('[data-testid="health-recheck"]');
    await button.trigger("click");
    await flushPromises();

    expect(mocks.api.getDependencyHealth).toHaveBeenCalledTimes(2);
  });

  it("logs the operator out when the backend returns 401", async () => {
    const failure = new Error("unauthorized");
    failure.response = { status: 401, data: { detail: { error: "no auth" } } };
    mocks.api.getDependencyHealth.mockRejectedValueOnce(failure);

    mountPage();
    await flushPromises();

    expect(mocks.adminStore.logout).toHaveBeenCalledTimes(1);
    expect(mocks.router.push).toHaveBeenCalledWith("/login");
  });

  it("treats a 403 response the same as 401 (force re-login)", async () => {
    const failure = new Error("forbidden");
    failure.response = { status: 403, data: { detail: { error: "denied" } } };
    mocks.api.getDependencyHealth.mockRejectedValueOnce(failure);

    mountPage();
    await flushPromises();

    expect(mocks.adminStore.logout).toHaveBeenCalledTimes(1);
    expect(mocks.router.push).toHaveBeenCalledWith("/login");
  });

  it("redirects to /login when no admin token is present", async () => {
    mocks.adminStore.token = "";
    mountPage();
    await flushPromises();

    expect(mocks.router.push).toHaveBeenCalledWith("/login");
    expect(mocks.api.getDependencyHealth).not.toHaveBeenCalled();
  });

  it("kicks off auto-refresh when the interval is changed and stops when reset", async () => {
    mocks.api.getDependencyHealth.mockResolvedValue(SAMPLE_PAYLOAD);
    const wrapper = mountPage();
    await flushPromises();

    expect(mocks.api.getDependencyHealth).toHaveBeenCalledTimes(1);

    const select = wrapper.get('[data-el-select="true"]');
    await select.setValue(30);
    await flushPromises();

    vi.advanceTimersByTime(60_000);
    await flushPromises();
    // Two ticks of 30 s each fired while auto-refresh was on.
    expect(mocks.api.getDependencyHealth).toHaveBeenCalledTimes(3);

    await select.setValue(0);
    await flushPromises();

    vi.advanceTimersByTime(120_000);
    await flushPromises();
    // No additional calls after auto-refresh was reset to "Off".
    expect(mocks.api.getDependencyHealth).toHaveBeenCalledTimes(3);
  });

  it("clears the auto-refresh timer on unmount", async () => {
    mocks.api.getDependencyHealth.mockResolvedValue(SAMPLE_PAYLOAD);
    const wrapper = mountPage();
    await flushPromises();

    const select = wrapper.get('[data-el-select="true"]');
    await select.setValue(30);
    await flushPromises();

    expect(mocks.api.getDependencyHealth).toHaveBeenCalledTimes(1);

    wrapper.unmount();

    vi.advanceTimersByTime(120_000);
    await flushPromises();
    // The interval was cancelled by onBeforeUnmount; no further refreshes.
    expect(mocks.api.getDependencyHealth).toHaveBeenCalledTimes(1);
  });

  it("surfaces a non-auth backend error via ElMessage and lastError when not silent", async () => {
    // Drives the catch-block branch at health.vue:77-84 — non-401/403
    // failure must (a) prefer ``error.response.data.detail.error`` for
    // the human-readable detail, (b) fall through ``ElMessage.error``
    // since this is a manual reload (silent=false). Existing tests
    // cover the 401/403 logout paths but stop at the early ``return``
    // — those branches never reach the detail-extraction or
    // toast-emission below.
    const ElMessage = await getElMessage();
    const errorSpy = vi.spyOn(ElMessage, "error").mockImplementation(noop);

    const failure = new Error("boom");
    failure.response = {
      status: 500,
      data: { detail: { error: "upstream probe is down" } },
    };
    mocks.api.getDependencyHealth.mockRejectedValueOnce(failure);

    const wrapper = mountPage();
    await flushPromises();

    expect(mocks.adminStore.logout).not.toHaveBeenCalled();
    expect(wrapper.vm.lastError).toBe("upstream probe is down");
    expect(errorSpy).toHaveBeenCalledWith("upstream probe is down");

    errorSpy.mockRestore();
  });

  it("falls back to error.message when the response carries no detail.error", async () => {
    // Same catch-block as above but with no structured detail —
    // exercises the ``error.message`` middle term of the
    // ``detail = ... || ... || ...`` chain.
    const ElMessage = await getElMessage();
    const errorSpy = vi.spyOn(ElMessage, "error").mockImplementation(noop);

    const failure = new Error("network fell over");
    failure.response = { status: 502, data: {} };
    mocks.api.getDependencyHealth.mockRejectedValueOnce(failure);

    const wrapper = mountPage();
    await flushPromises();

    expect(wrapper.vm.lastError).toBe("network fell over");
    expect(errorSpy).toHaveBeenCalledWith("network fell over");

    errorSpy.mockRestore();
  });

  it("uses the default detail when both response.detail and error.message are missing", async () => {
    // Exercises the third (final-fallback) term of the detail chain.
    const ElMessage = await getElMessage();
    const errorSpy = vi.spyOn(ElMessage, "error").mockImplementation(noop);

    // Construct an error with NO .message and NO .response — the chain
    // collapses to the hard-coded fallback string.
    const failure = Object.assign(Object.create(null), {});
    mocks.api.getDependencyHealth.mockRejectedValueOnce(failure);

    const wrapper = mountPage();
    await flushPromises();

    expect(wrapper.vm.lastError).toBe("Failed to load dependency health");
    expect(errorSpy).toHaveBeenCalledWith("Failed to load dependency health");

    errorSpy.mockRestore();
  });

  it("does NOT emit ElMessage.error when the failure happens during a silent auto-refresh", async () => {
    // Pin the ``if (!silent)`` gate at health.vue:82. Auto-refresh
    // ticks pass ``{ silent: true }`` so a flaky probe doesn't
    // toast-spam every 30 seconds; only ``lastError`` should update.
    const ElMessage = await getElMessage();
    const errorSpy = vi.spyOn(ElMessage, "error").mockImplementation(noop);

    // First load succeeds so the page renders normally.
    mocks.api.getDependencyHealth.mockResolvedValueOnce(SAMPLE_PAYLOAD);
    const wrapper = mountPage();
    await flushPromises();

    // Switch to a 30s auto-refresh interval; the next tick errors out.
    const select = wrapper.get('[data-el-select="true"]');
    await select.setValue(30);
    await flushPromises();

    const failure = new Error("transient");
    failure.response = { status: 502, data: {} };
    mocks.api.getDependencyHealth.mockRejectedValueOnce(failure);

    vi.advanceTimersByTime(30_000);
    await flushPromises();

    expect(wrapper.vm.lastError).toBe("transient");
    // Critically: no toast — silent=true path.
    expect(errorSpy).not.toHaveBeenCalled();

    errorSpy.mockRestore();
  });
});
