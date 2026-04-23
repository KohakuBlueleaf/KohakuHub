import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ElementPlusStubs } from "../helpers/vue";

const mocks = vi.hoisted(() => ({
  router: {
    push: vi.fn(),
  },
  adminStore: {
    login: vi.fn(),
  },
}));

vi.mock("vue-router", () => ({
  useRouter: () => mocks.router,
}));

vi.mock("@/stores/admin", () => ({
  useAdminStore: () => mocks.adminStore,
}));

vi.mock("element-plus", async () => {
  const actual = await vi.importActual("element-plus");
  return actual;
});

import LoginPage from "@/pages/login.vue";

describe("admin login page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  function mountPage() {
    return mount(LoginPage, {
      global: {
        stubs: ElementPlusStubs,
      },
    });
  }

  it("rejects empty login attempts before calling the store", async () => {
    const wrapper = mountPage();

    await wrapper.get("form").trigger("submit");

    expect(mocks.adminStore.login).not.toHaveBeenCalled();
    expect(mocks.router.push).not.toHaveBeenCalled();
  });

  it("logs in successfully and redirects to the dashboard", async () => {
    mocks.adminStore.login.mockResolvedValue(true);
    const wrapper = mountPage();

    await wrapper.get('input[placeholder="Admin Token"]').setValue("token-123");
    await wrapper.get("form").trigger("submit");
    await flushPromises();

    expect(mocks.adminStore.login).toHaveBeenCalledWith("token-123");
    expect(mocks.router.push).toHaveBeenCalledWith("/");
  });

  it("clears the token field when verification fails or the API errors", async () => {
    mocks.adminStore.login.mockResolvedValueOnce(false);
    const wrapper = mountPage();

    const input = wrapper.get('input[placeholder="Admin Token"]');
    await input.setValue("bad-token");
    await wrapper.get("form").trigger("submit");
    await flushPromises();

    expect(input.element.value).toBe("");
    expect(mocks.router.push).not.toHaveBeenCalled();

    mocks.adminStore.login.mockRejectedValueOnce({
      response: {
        data: {
          detail: {
            error: "Backend rejected token",
          },
        },
      },
    });

    await input.setValue("exploded-token");
    await wrapper.get("form").trigger("submit");
    await flushPromises();

    expect(input.element.value).toBe("");
    expect(mocks.router.push).not.toHaveBeenCalledWith("/");
  });
});
