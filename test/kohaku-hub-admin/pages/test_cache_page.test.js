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
    getCacheStats: vi.fn(),
    resetCacheMetrics: vi.fn(),
  },
}));

vi.mock("vue-router", () => ({
  useRouter: () => mocks.router,
}));

vi.mock("@/stores/admin", () => ({
  useAdminStore: () => mocks.adminStore,
}));

vi.mock("@/utils/api", () => ({
  getCacheStats: (...args) => mocks.api.getCacheStats(...args),
  resetCacheMetrics: (...args) => mocks.api.resetCacheMetrics(...args),
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

// We don't ``vi.mock("element-plus", ...)`` because the package's resolved
// module identity differs between the test runner's imports and the SFC's
// imports (the bundler resolves through ``./es/index.mjs``, vitest's
// transformer keeps a pre-loaded copy from setup, and the test-file mock
// fails to intercept the SFC's reference). We can't ``import`` it
// statically either: ``element-plus`` is in ``src/kohaku-hub-admin/node_modules``
// while this test file lives outside that package and Vite's import-analysis
// rejects the bare specifier. So we lazily resolve it via ``vi.importActual``
// in ``beforeEach`` (which honors the SFC's resolution path because vitest
// reuses the SFC's plugin chain) and ``spyOn`` the methods we need.
import CachePage from "@/pages/cache.vue";

const messageBoxConfirmSpy = vi.fn();
const messageSuccessSpy = vi.fn();
const messageErrorSpy = vi.fn();
let elementPlusModule;

const ENABLED_PAYLOAD = {
  metrics: {
    configured_enabled: true,
    client_initialized: true,
    namespace: "kh",
    hits: { lakefs: 120, repo: 45 },
    misses: { lakefs: 8, repo: 12 },
    errors: {},
    set_count: { lakefs: 8, repo: 12 },
    invalidate_count: { repo: 3 },
    singleflight_contention: 5,
    last_flush_run_id: "abcdef0123456789abcdef0123456789abcdef01",
    last_flush_at_ms: 1_700_000_000_000,
    last_flushed_keys: 7,
  },
  memory: {
    available: true,
    used_memory: 4_194_304,
    used_memory_human: "4.00M",
    maxmemory: 536_870_912,
    maxmemory_human: "512.00M",
    maxmemory_policy: "allkeys-lfu",
    evicted_keys: 0,
  },
};

const DEGRADED_PAYLOAD = {
  metrics: {
    configured_enabled: true,
    // Configured on but client not initialized — silent-degradation state.
    client_initialized: false,
    namespace: "kh",
    hits: {},
    misses: {},
    errors: {},
    set_count: {},
    invalidate_count: {},
    singleflight_contention: 0,
    last_flush_run_id: null,
    last_flush_at_ms: null,
    last_flushed_keys: 0,
  },
  memory: { available: false, reason: "client not initialized" },
};

const DISABLED_PAYLOAD = {
  metrics: {
    configured_enabled: false,
    client_initialized: false,
    namespace: "kh",
    hits: {},
    misses: {},
    errors: {},
    set_count: {},
    invalidate_count: {},
    singleflight_contention: 0,
    last_flush_run_id: null,
    last_flush_at_ms: null,
    last_flushed_keys: 0,
  },
  memory: { available: false, reason: "client not initialized" },
};

const noop = () => {};
const noopDirective = {
  mounted: noop,
  updated: noop,
  beforeUnmount: noop,
};

function mountPage() {
  return mount(CachePage, {
    global: {
      stubs: ElementPlusStubs,
      directives: {
        loading: noopDirective,
      },
    },
  });
}

describe("admin cache page", () => {
  beforeEach(async () => {
    vi.useFakeTimers();
    mocks.router.push.mockReset();
    mocks.adminStore.logout.mockReset();
    mocks.adminStore.token = "admin-token";
    mocks.api.getCacheStats.mockReset();
    mocks.api.resetCacheMetrics.mockReset();
    messageBoxConfirmSpy.mockReset();
    messageSuccessSpy.mockReset();
    messageErrorSpy.mockReset();

    if (!elementPlusModule) {
      elementPlusModule = await vi.importActual("element-plus");
    }

    // Hijack ElMessageBox.confirm and ElMessage.{success,error} via
    // spyOn so the SFC's ``await ElMessageBox.confirm(...)`` lands in
    // our test-controllable spy. ``afterEach -> vi.restoreAllMocks()``
    // (declared in setup/vitest.setup.js) restores the originals after
    // each test.
    vi.spyOn(elementPlusModule.ElMessageBox, "confirm").mockImplementation(
      (...args) => messageBoxConfirmSpy(...args),
    );
    vi.spyOn(elementPlusModule.ElMessage, "success").mockImplementation(
      (...args) => messageSuccessSpy(...args),
    );
    vi.spyOn(elementPlusModule.ElMessage, "error").mockImplementation(
      (...args) => messageErrorSpy(...args),
    );
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("loads stats on mount and renders one row per cache namespace", async () => {
    mocks.api.getCacheStats.mockResolvedValue(ENABLED_PAYLOAD);
    const wrapper = mountPage();
    await flushPromises();

    expect(mocks.api.getCacheStats).toHaveBeenCalledWith("admin-token");

    const overall = wrapper.get('[data-testid="cache-overall"]');
    expect(overall.text()).toContain("Enabled");
    expect(overall.text()).toContain("kh");
    expect(overall.text()).toContain("4.00M");
    expect(overall.text()).toContain("allkeys-lfu");

    const bootstrap = wrapper.get('[data-testid="cache-bootstrap"]');
    expect(bootstrap.text()).toContain("abcdef012345");

    // namespaces table picks up exactly one row per namespace key the
    // backend returned, regardless of which counter dict it appears in.
    // ``cache-namespace-table`` is the wrapping card; the actual <table>
    // is inside it (rendered by the ElTable stub with data-row-count).
    const card = wrapper.get('[data-testid="cache-namespace-table"]');
    const table = card.get('[data-el-table="true"]');
    expect(table.attributes("data-row-count")).toBe("2");
    expect(card.text()).toContain("lakefs");
    expect(card.text()).toContain("repo");
  });

  it("shows 'Degraded' when configured-on but client not initialized", async () => {
    mocks.api.getCacheStats.mockResolvedValue(DEGRADED_PAYLOAD);
    const wrapper = mountPage();
    await flushPromises();

    const overall = wrapper.get('[data-testid="cache-overall"]');
    expect(overall.text()).toContain("Degraded");
  });

  it("shows 'Disabled' when configured off, and disables the reset button", async () => {
    mocks.api.getCacheStats.mockResolvedValue(DISABLED_PAYLOAD);
    const wrapper = mountPage();
    await flushPromises();

    const overall = wrapper.get('[data-testid="cache-overall"]');
    expect(overall.text()).toContain("Disabled");

    const resetButton = wrapper.get('[data-testid="cache-reset-metrics"]');
    expect(resetButton.attributes("disabled")).toBeDefined();
  });

  it("re-fetches when the user clicks Refresh", async () => {
    mocks.api.getCacheStats.mockResolvedValue(ENABLED_PAYLOAD);
    const wrapper = mountPage();
    await flushPromises();

    const button = wrapper.get('[data-testid="cache-refresh"]');
    await button.trigger("click");
    await flushPromises();

    expect(mocks.api.getCacheStats).toHaveBeenCalledTimes(2);
  });

  it("calls reset endpoint after the operator confirms the dialog", async () => {
    mocks.api.getCacheStats.mockResolvedValue(ENABLED_PAYLOAD);
    mocks.api.resetCacheMetrics.mockResolvedValue({ reset: true });
    messageBoxConfirmSpy.mockResolvedValue("confirm");

    const wrapper = mountPage();
    await flushPromises();

    const resetButton = wrapper.get('[data-testid="cache-reset-metrics"]');
    await resetButton.trigger("click");
    await flushPromises();

    expect(messageBoxConfirmSpy).toHaveBeenCalled();
    expect(mocks.api.resetCacheMetrics).toHaveBeenCalledWith("admin-token");
    // After reset, page re-loads stats silently to refresh the counters.
    expect(mocks.api.getCacheStats).toHaveBeenCalledTimes(2);
  });

  it("does not call reset endpoint when the operator cancels the dialog", async () => {
    mocks.api.getCacheStats.mockResolvedValue(ENABLED_PAYLOAD);
    messageBoxConfirmSpy.mockRejectedValue(new Error("user cancelled"));

    const wrapper = mountPage();
    await flushPromises();

    const resetButton = wrapper.get('[data-testid="cache-reset-metrics"]');
    await resetButton.trigger("click");
    await flushPromises();

    expect(messageBoxConfirmSpy).toHaveBeenCalled();
    expect(mocks.api.resetCacheMetrics).not.toHaveBeenCalled();
  });

  it("logs the operator out when the backend returns 401", async () => {
    const failure = new Error("unauthorized");
    failure.response = { status: 401, data: { detail: "no auth" } };
    mocks.api.getCacheStats.mockRejectedValueOnce(failure);

    mountPage();
    await flushPromises();

    expect(mocks.adminStore.logout).toHaveBeenCalledTimes(1);
    expect(mocks.router.push).toHaveBeenCalledWith("/login");
  });

  it("treats a 403 response the same as 401", async () => {
    const failure = new Error("forbidden");
    failure.response = { status: 403, data: { detail: "denied" } };
    mocks.api.getCacheStats.mockRejectedValueOnce(failure);

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
    expect(mocks.api.getCacheStats).not.toHaveBeenCalled();
  });

  it("kicks off auto-refresh when the interval is changed and stops when reset", async () => {
    mocks.api.getCacheStats.mockResolvedValue(ENABLED_PAYLOAD);
    const wrapper = mountPage();
    await flushPromises();

    expect(mocks.api.getCacheStats).toHaveBeenCalledTimes(1);

    const select = wrapper.get('[data-el-select="true"]');
    await select.setValue(15);
    await flushPromises();

    vi.advanceTimersByTime(45_000);
    await flushPromises();
    // Three ticks of 15 s each fired while auto-refresh was on.
    expect(mocks.api.getCacheStats).toHaveBeenCalledTimes(4);

    await select.setValue(0);
    await flushPromises();

    vi.advanceTimersByTime(120_000);
    await flushPromises();
    expect(mocks.api.getCacheStats).toHaveBeenCalledTimes(4);
  });

  it("clears the auto-refresh timer on unmount", async () => {
    mocks.api.getCacheStats.mockResolvedValue(ENABLED_PAYLOAD);
    const wrapper = mountPage();
    await flushPromises();

    const select = wrapper.get('[data-el-select="true"]');
    await select.setValue(15);
    await flushPromises();

    expect(mocks.api.getCacheStats).toHaveBeenCalledTimes(1);

    wrapper.unmount();

    vi.advanceTimersByTime(120_000);
    await flushPromises();
    expect(mocks.api.getCacheStats).toHaveBeenCalledTimes(1);
  });
});

