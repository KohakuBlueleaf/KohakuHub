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
    listAdminSessions: vi.fn(),
    listAdminTokens: vi.fn(),
    listAdminSshKeys: vi.fn(),
    revokeAdminSession: vi.fn(),
    revokeAdminToken: vi.fn(),
    revokeAdminSshKey: vi.fn(),
    revokeAdminSessionsBulk: vi.fn(),
  },
  dialogs: {
    confirmDialog: vi.fn(),
    showError: vi.fn(),
    showSuccess: vi.fn(),
    showWarning: vi.fn(),
    showInfo: vi.fn(),
  },
}));

vi.mock("vue-router", () => ({
  useRouter: () => mocks.router,
}));

vi.mock("@/stores/admin", () => ({
  useAdminStore: () => mocks.adminStore,
}));

vi.mock("@/utils/api", () => ({
  listAdminSessions: (...args) => mocks.api.listAdminSessions(...args),
  listAdminTokens: (...args) => mocks.api.listAdminTokens(...args),
  listAdminSshKeys: (...args) => mocks.api.listAdminSshKeys(...args),
  revokeAdminSession: (...args) => mocks.api.revokeAdminSession(...args),
  revokeAdminToken: (...args) => mocks.api.revokeAdminToken(...args),
  revokeAdminSshKey: (...args) => mocks.api.revokeAdminSshKey(...args),
  revokeAdminSessionsBulk: (...args) =>
    mocks.api.revokeAdminSessionsBulk(...args),
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

vi.mock("@/utils/dialogs", () => ({
  confirmDialog: (...args) => mocks.dialogs.confirmDialog(...args),
  showError: (...args) => mocks.dialogs.showError(...args),
  showSuccess: (...args) => mocks.dialogs.showSuccess(...args),
  showWarning: (...args) => mocks.dialogs.showWarning(...args),
  showInfo: (...args) => mocks.dialogs.showInfo(...args),
}));

vi.mock("element-plus", async () => {
  const actual = await vi.importActual("element-plus");
  return actual;
});

import CredentialsPage from "@/pages/credentials.vue";

const SESSIONS_PAYLOAD = {
  total: 2,
  limit: 20,
  offset: 0,
  sessions: [
    {
      id: 1,
      user_id: 11,
      username: "owner",
      created_at: "2026-04-01T00:00:00Z",
      expires_at: "2099-01-01T00:00:00Z",
      expired: false,
    },
    {
      id: 2,
      user_id: 12,
      username: "outsider",
      created_at: "2025-01-01T00:00:00Z",
      expires_at: "2025-02-01T00:00:00Z",
      expired: true,
    },
  ],
};

const TOKENS_PAYLOAD = {
  total: 1,
  limit: 20,
  offset: 0,
  tokens: [
    {
      id: 7,
      user_id: 11,
      username: "owner",
      name: "ci-token",
      created_at: "2026-03-01T00:00:00Z",
      last_used: null,
    },
  ],
};

const SSH_KEYS_PAYLOAD = {
  total: 1,
  limit: 20,
  offset: 0,
  ssh_keys: [
    {
      id: 3,
      user_id: 11,
      username: "owner",
      key_type: "ssh-ed25519",
      fingerprint: "SHA256:fake-fingerprint",
      title: "Workstation",
      created_at: "2026-02-01T00:00:00Z",
      last_used: "2026-04-01T00:00:00Z",
    },
  ],
};

const noopDirective = {
  mounted: () => {},
  updated: () => {},
  beforeUnmount: () => {},
};

function mountPage() {
  return mount(CredentialsPage, {
    global: {
      stubs: ElementPlusStubs,
      directives: { loading: noopDirective },
    },
  });
}

describe("admin credentials page", () => {
  beforeEach(() => {
    mocks.router.push.mockReset();
    mocks.adminStore.logout.mockReset();
    mocks.adminStore.token = "admin-token";
    Object.values(mocks.api).forEach((fn) => fn.mockReset());
    Object.values(mocks.dialogs).forEach((fn) => fn.mockReset());

    mocks.api.listAdminSessions.mockResolvedValue(SESSIONS_PAYLOAD);
    mocks.api.listAdminTokens.mockResolvedValue(TOKENS_PAYLOAD);
    mocks.api.listAdminSshKeys.mockResolvedValue(SSH_KEYS_PAYLOAD);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("loads sessions on mount and renders the bulk-revoke entry point", async () => {
    const wrapper = mountPage();
    await flushPromises();

    expect(mocks.api.listAdminSessions).toHaveBeenCalledTimes(1);
    expect(mocks.api.listAdminSessions).toHaveBeenCalledWith("admin-token", {
      user: undefined,
      activeOnly: undefined,
      limit: 20,
      offset: 0,
    });
    expect(mocks.api.listAdminTokens).not.toHaveBeenCalled();
    expect(mocks.api.listAdminSshKeys).not.toHaveBeenCalled();

    expect(
      wrapper.find('[data-testid="credentials-open-bulk"]').exists(),
    ).toBe(true);
  });

  it("re-fetches sessions when Apply is clicked with a username filter", async () => {
    const wrapper = mountPage();
    await flushPromises();

    mocks.api.listAdminSessions.mockClear();

    const userFilter = wrapper.get(
      '[data-testid="credentials-user-filter"] input',
    );
    await userFilter.setValue("outsider");

    await wrapper.get('[data-testid="credentials-apply"]').trigger("click");
    await flushPromises();

    expect(mocks.api.listAdminSessions).toHaveBeenCalledTimes(1);
    const [, options] = mocks.api.listAdminSessions.mock.calls[0];
    expect(options.user).toBe("outsider");
    expect(options.limit).toBe(20);
    expect(options.offset).toBe(0);
  });

  it("loads API tokens when the operator switches to the Tokens tab", async () => {
    const wrapper = mountPage();
    await flushPromises();

    expect(mocks.api.listAdminTokens).not.toHaveBeenCalled();

    wrapper.vm.activeTab = "tokens";
    await flushPromises();

    expect(mocks.api.listAdminTokens).toHaveBeenCalledTimes(1);
    expect(mocks.api.listAdminSshKeys).not.toHaveBeenCalled();
  });

  it("loads SSH keys when the operator switches to the SSH Keys tab", async () => {
    const wrapper = mountPage();
    await flushPromises();

    wrapper.vm.activeTab = "ssh-keys";
    await flushPromises();

    expect(mocks.api.listAdminSshKeys).toHaveBeenCalledTimes(1);
  });

  it("logs the operator out when the backend returns 401", async () => {
    const failure = new Error("unauthorized");
    failure.response = { status: 401, data: { detail: { error: "no auth" } } };
    mocks.api.listAdminSessions.mockRejectedValueOnce(failure);

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
    expect(mocks.api.listAdminSessions).not.toHaveBeenCalled();
  });

  it("revokes a single session after the operator confirms", async () => {
    mocks.dialogs.confirmDialog.mockResolvedValueOnce(undefined);
    mocks.api.revokeAdminSession.mockResolvedValueOnce({ revoked: 1 });

    const wrapper = mountPage();
    await flushPromises();

    await wrapper.vm.revokeSession({ id: 5, username: "outsider" });
    await flushPromises();

    expect(mocks.dialogs.confirmDialog).toHaveBeenCalledTimes(1);
    expect(mocks.api.revokeAdminSession).toHaveBeenCalledWith(
      "admin-token",
      5,
    );
    expect(mocks.dialogs.showSuccess).toHaveBeenCalledWith("Revoked 1");
    // Reload after success: original mount call + one re-fetch.
    expect(mocks.api.listAdminSessions).toHaveBeenCalledTimes(2);
  });

  it("does NOT call the API when the operator cancels the revoke dialog", async () => {
    mocks.dialogs.confirmDialog.mockRejectedValueOnce("cancel");

    const wrapper = mountPage();
    await flushPromises();

    await wrapper.vm.revokeSession({ id: 5, username: "outsider" });
    await flushPromises();

    expect(mocks.api.revokeAdminSession).not.toHaveBeenCalled();
    expect(mocks.dialogs.showSuccess).not.toHaveBeenCalled();
  });

  it("revokes a single token and reloads the tokens tab", async () => {
    mocks.dialogs.confirmDialog.mockResolvedValueOnce(undefined);
    mocks.api.revokeAdminToken.mockResolvedValueOnce({ revoked: 1 });

    const wrapper = mountPage();
    await flushPromises();
    wrapper.vm.activeTab = "tokens";
    await flushPromises();

    await wrapper.vm.revokeToken({
      id: 7,
      username: "owner",
      name: "ci-token",
    });
    await flushPromises();

    expect(mocks.api.revokeAdminToken).toHaveBeenCalledWith("admin-token", 7);
    expect(mocks.api.listAdminTokens).toHaveBeenCalledTimes(2);
  });

  it("revokes a single SSH key and reloads the keys tab", async () => {
    mocks.dialogs.confirmDialog.mockResolvedValueOnce(undefined);
    mocks.api.revokeAdminSshKey.mockResolvedValueOnce({ revoked: 1 });

    const wrapper = mountPage();
    await flushPromises();
    wrapper.vm.activeTab = "ssh-keys";
    await flushPromises();

    await wrapper.vm.revokeSshKey({
      id: 3,
      username: "owner",
      title: "Workstation",
      fingerprint: "SHA256:fake",
    });
    await flushPromises();

    expect(mocks.api.revokeAdminSshKey).toHaveBeenCalledWith("admin-token", 3);
    expect(mocks.api.listAdminSshKeys).toHaveBeenCalledTimes(2);
  });

  it("blocks bulk revoke when neither user nor before_ts is provided", async () => {
    const wrapper = mountPage();
    await flushPromises();

    wrapper.vm.bulkRevokeForm = { user: "", beforeTs: "" };
    await wrapper.vm.submitBulkRevoke();
    await flushPromises();

    expect(mocks.dialogs.confirmDialog).not.toHaveBeenCalled();
    expect(mocks.api.revokeAdminSessionsBulk).not.toHaveBeenCalled();
    expect(mocks.dialogs.showWarning).toHaveBeenCalledTimes(1);
  });

  it("submits bulk revoke with the user filter when confirmed", async () => {
    mocks.dialogs.confirmDialog.mockResolvedValueOnce(undefined);
    mocks.api.revokeAdminSessionsBulk.mockResolvedValueOnce({ revoked: 3 });

    const wrapper = mountPage();
    await flushPromises();

    wrapper.vm.bulkRevokeForm = { user: "outsider", beforeTs: "" };
    await wrapper.vm.submitBulkRevoke();
    await flushPromises();

    expect(mocks.api.revokeAdminSessionsBulk).toHaveBeenCalledWith(
      "admin-token",
      { user: "outsider" },
    );
    expect(mocks.api.listAdminSessions).toHaveBeenCalledTimes(2);
    expect(wrapper.vm.bulkRevokeOpen).toBe(false);
  });

  it("surfaces backend errors via showError when revoke fails with 5xx", async () => {
    mocks.dialogs.confirmDialog.mockResolvedValueOnce(undefined);
    const failure = new Error("server error");
    failure.response = { status: 500, data: {} };
    mocks.api.revokeAdminSession.mockRejectedValueOnce(failure);

    const wrapper = mountPage();
    await flushPromises();

    await wrapper.vm.revokeSession({ id: 5, username: "owner" });
    await flushPromises();

    expect(mocks.dialogs.showError).toHaveBeenCalled();
    expect(mocks.adminStore.logout).not.toHaveBeenCalled();
  });

  it("returns silently when the bulk-revoke confirm dialog is cancelled", async () => {
    // ``confirmDialog`` rejects (per the project's helper contract) when
    // the user clicks Cancel. The inner ``catch { return; }`` at
    // credentials.vue:215-217 must short-circuit *before* the API call,
    // never showWarning the user (they explicitly chose to cancel).
    mocks.dialogs.confirmDialog.mockRejectedValueOnce(new Error("cancelled"));

    const wrapper = mountPage();
    await flushPromises();

    wrapper.vm.bulkRevokeForm = { user: "outsider", beforeTs: "" };
    await wrapper.vm.submitBulkRevoke();
    await flushPromises();

    expect(mocks.api.revokeAdminSessionsBulk).not.toHaveBeenCalled();
    // No warning toast — cancellation is a clean exit, not an error.
    expect(mocks.dialogs.showWarning).not.toHaveBeenCalled();
    // Dialog stays open so the operator can re-attempt without
    // re-typing the form.
    expect(wrapper.vm.bulkRevokeOpen).toBe(false);
  });

  it("surfaces a backend error via handleApiError when bulk revoke API fails", async () => {
    // Pin the error-path branch at credentials.vue:224-226 — the bulk
    // revoke API throws AFTER the operator confirmed; we route it
    // through ``handleApiError`` which (for non-401/403 failures) calls
    // ``showError`` with a friendly message. Existing
    // "surfaces backend errors via showError when revoke fails with
    // 5xx" covers the *single*-revoke path; this is the bulk variant.
    mocks.dialogs.confirmDialog.mockResolvedValueOnce(undefined);
    const failure = new Error("bulk failed");
    failure.response = { status: 500, data: {} };
    mocks.api.revokeAdminSessionsBulk.mockRejectedValueOnce(failure);

    const wrapper = mountPage();
    await flushPromises();

    wrapper.vm.bulkRevokeForm = { user: "outsider", beforeTs: "" };
    await wrapper.vm.submitBulkRevoke();
    await flushPromises();

    expect(mocks.api.revokeAdminSessionsBulk).toHaveBeenCalledTimes(1);
    expect(mocks.dialogs.showError).toHaveBeenCalled();
    expect(mocks.adminStore.logout).not.toHaveBeenCalled();
  });
});
