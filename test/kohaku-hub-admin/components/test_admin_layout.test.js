import { mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { defineComponent, h } from "vue";

import { ElementPlusStubs } from "../helpers/vue";

const mocks = vi.hoisted(() => ({
  router: {
    push: vi.fn(),
  },
  route: {
    path: "/repositories",
  },
  adminStore: {
    logout: vi.fn(),
  },
  themeStore: {
    isDark: false,
    toggle: vi.fn(),
  },
  globalSearch: {
    openDialog: vi.fn(),
  },
}));

vi.mock("vue-router", () => ({
  useRouter: () => mocks.router,
  useRoute: () => mocks.route,
}));

vi.mock("@/stores/admin", () => ({
  useAdminStore: () => mocks.adminStore,
}));

vi.mock("@/stores/theme", () => ({
  useThemeStore: () => mocks.themeStore,
}));

vi.mock("element-plus", async () => {
  const actual = await vi.importActual("element-plus");
  return actual;
});

vi.mock("@/components/GlobalSearch.vue", () => ({
  default: defineComponent({
    name: "GlobalSearch",
    setup(_, { expose }) {
      expose({
        openDialog: mocks.globalSearch.openDialog,
      });

      return () => h("div", { "data-global-search": "true" });
    },
  }),
}));

import AdminLayout from "@/components/AdminLayout.vue";

describe("AdminLayout", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.route.path = "/repositories";
    mocks.themeStore.isDark = false;
  });

  function mountLayout() {
    return mount(AdminLayout, {
      slots: {
        default: '<section data-slot-content="true">Dashboard content</section>',
      },
      global: {
        stubs: ElementPlusStubs,
      },
    });
  }

  it("renders the admin navigation, opens global search, and toggles theme", async () => {
    const wrapper = mountLayout();

    expect(wrapper.text()).toContain("Admin Portal");
    expect(wrapper.text()).toContain("Repositories");
    expect(wrapper.text()).toContain("Quota Overview");
    expect(wrapper.find('[data-slot-content="true"]').exists()).toBe(true);

    await wrapper
      .findAll("button")
      .find((button) => button.text().includes("Search"))
      .trigger("click");
    await wrapper
      .findAll("button")
      .find((button) => button.attributes("data-circle") === "true")
      .trigger("click");

    expect(mocks.globalSearch.openDialog).toHaveBeenCalledTimes(1);
    expect(mocks.themeStore.toggle).toHaveBeenCalledTimes(1);
    expect(wrapper.find('[data-global-search="true"]').exists()).toBe(true);
  });

  it("logs out and returns to the login page", async () => {
    const wrapper = mountLayout();

    await wrapper
      .findAll("button")
      .find((button) => button.text().includes("Logout"))
      .trigger("click");

    expect(mocks.adminStore.logout).toHaveBeenCalledTimes(1);
    expect(mocks.router.push).toHaveBeenCalledWith("/login");
  });
});
