import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ElementPlusStubs, RouterLinkStub } from "../helpers/vue";
import axios from "@/testing/axios";
import { createMemoryHistory, createRouter } from "@/testing/router";

import { useAuthStore } from "@/stores/auth";
import LoginPage from "@/pages/login.vue";
import RegisterPage from "@/pages/register.vue";

describe("auth pages", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setActivePinia(createPinia());
    vi.spyOn(axios, "get").mockResolvedValue({
      data: { invitation_only: false },
    });
  });

  async function createTestRouter(initialPath) {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: "/login", component: { template: "<div />" } },
        { path: "/register", component: { template: "<div />" } },
        { path: "/:pathMatch(.*)*", component: { template: "<div />" } },
      ],
    });

    await router.push(initialPath);
    await router.isReady();
    return router;
  }

  function mountPage(component, router) {
    return mount(component, {
      global: {
        plugins: [router],
        stubs: {
          ...ElementPlusStubs,
          RouterLink: RouterLinkStub,
        },
      },
    });
  }

  it("loads site config and logs in with the return URL", async () => {
    const router = await createTestRouter(
      `/login?return=${encodeURIComponent("/models/mai_lin/lineart-caption-base")}`,
    );
    const pushSpy = vi.spyOn(router, "push");
    const authStore = useAuthStore();
    authStore.login = vi.fn().mockResolvedValue({ ok: true });

    const wrapper = mountPage(LoginPage, router);
    await flushPromises();

    await wrapper
      .find('input[placeholder="Enter your username"]')
      .setValue("mai_lin");
    await wrapper
      .find('input[placeholder="Enter your password"]')
      .setValue("KohakuDev123!");
    await wrapper
      .findAll("button")
      .find((button) => button.text().includes("Login"))
      .trigger("click");
    await flushPromises();

    expect(axios.get).toHaveBeenCalledWith("/api/site-config");
    expect(authStore.login).toHaveBeenCalledWith({
      username: "mai_lin",
      password: "KohakuDev123!",
    });
    expect(pushSpy).toHaveBeenCalledWith(
      "/models/mai_lin/lineart-caption-base",
    );
  });

  it("shows invitation-only login guidance when registration is closed", async () => {
    const router = await createTestRouter("/login");
    axios.get.mockResolvedValueOnce({
      data: { invitation_only: true },
    });

    const authStore = useAuthStore();
    authStore.login = vi.fn().mockRejectedValue(new Error("bad credentials"));

    const wrapper = mountPage(LoginPage, router);
    await flushPromises();

    expect(wrapper.text()).toContain("Registration is invitation-only");
    expect(wrapper.text()).not.toContain("Sign up");
  });

  it("blocks registration without an invitation and routes visitors to login", async () => {
    const router = await createTestRouter("/register");
    const pushSpy = vi.spyOn(router, "push");
    axios.get.mockResolvedValueOnce({
      data: { invitation_only: true },
    });

    const wrapper = mountPage(RegisterPage, router);
    await flushPromises();

    expect(wrapper.text()).toContain("Registration is Invitation-Only");

    await wrapper
      .findAll("button")
      .find((button) => button.text().includes("Go to Login"))
      .trigger("click");

    expect(pushSpy).toHaveBeenCalledWith("/login");
  });

  it("registers with an invitation token and auto-logins verified users", async () => {
    const router = await createTestRouter(
      `/register?invitation=invite-123&return=${encodeURIComponent("/datasets/aurora-labs/street-sign-zh-en")}`,
    );
    const pushSpy = vi.spyOn(router, "push");
    const authStore = useAuthStore();
    authStore.register = vi.fn().mockResolvedValue({
      message: "Registration successful",
      email_verified: true,
    });
    authStore.login = vi.fn().mockResolvedValue({ ok: true });

    const wrapper = mountPage(RegisterPage, router);
    await flushPromises();

    await wrapper.find('input[placeholder="Choose a username"]').setValue("ivy_ops");
    await wrapper.find('input[placeholder="your@email.com"]').setValue("ivy@example.com");
    await wrapper.find('input[placeholder="Create a password"]').setValue("securepass");
    await wrapper
      .findAll("button")
      .find((button) => button.text().includes("Sign Up"))
      .trigger("click");
    await flushPromises();

    expect(authStore.register).toHaveBeenCalledWith({
      username: "ivy_ops",
      email: "ivy@example.com",
      password: "securepass",
      invitation_token: "invite-123",
    });
    expect(authStore.login).toHaveBeenCalledWith({
      username: "ivy_ops",
      password: "securepass",
    });
    expect(pushSpy).toHaveBeenCalledWith(
      "/datasets/aurora-labs/street-sign-zh-en",
    );
  });
});
