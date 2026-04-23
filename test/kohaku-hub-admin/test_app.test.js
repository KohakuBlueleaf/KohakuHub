import { mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  router: {
    push: vi.fn(),
    currentRoute: {
      value: {
        path: "/",
      },
    },
  },
  adminStore: {
    isAuthenticated: false,
  },
  themeStore: {
    init: vi.fn(),
  },
}));

vi.mock("vue-router", () => ({
  useRouter: () => mocks.router,
}));

vi.mock("@/stores/admin", () => ({
  useAdminStore: () => mocks.adminStore,
}));

vi.mock("@/stores/theme", () => ({
  useThemeStore: () => mocks.themeStore,
}));

import App from "@/App.vue";

describe("App shell", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.router.currentRoute.value.path = "/";
    mocks.adminStore.isAuthenticated = false;
  });

  function mountApp() {
    return mount(App, {
      global: {
        stubs: {
          RouterView: {
            name: "RouterView",
            template: '<main data-router-view="true" />',
          },
        },
      },
    });
  }

  it("initializes theme and redirects anonymous admins away from protected routes", () => {
    const wrapper = mountApp();

    expect(mocks.themeStore.init).toHaveBeenCalledTimes(1);
    expect(mocks.router.push).toHaveBeenCalledWith("/login");
    expect(wrapper.find('[data-router-view="true"]').exists()).toBe(true);
  });

  it("skips the login redirect when already authenticated or already on the login page", () => {
    mocks.adminStore.isAuthenticated = true;
    const authenticatedWrapper = mountApp();

    expect(mocks.router.push).not.toHaveBeenCalled();
    authenticatedWrapper.unmount();

    vi.clearAllMocks();
    mocks.adminStore.isAuthenticated = false;
    mocks.router.currentRoute.value.path = "/login";

    mountApp();

    expect(mocks.themeStore.init).toHaveBeenCalledTimes(1);
    expect(mocks.router.push).not.toHaveBeenCalled();
  });
});
