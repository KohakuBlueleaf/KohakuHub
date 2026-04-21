import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ElementPlusStubs,
  InvalidElFormStub,
  RouterLinkStub,
} from "../helpers/vue";
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

  function mountPage(component, router, extraStubs = {}) {
    return mount(component, {
      global: {
        plugins: [router],
        stubs: {
          ...ElementPlusStubs,
          ...extraStubs,
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

  it("logs in and falls back to the home page when no return URL is present", async () => {
    const router = await createTestRouter("/login");
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
      .setValue("secret");
    await wrapper
      .findAll("button")
      .find((button) => button.text().includes("Login"))
      .trigger("click");
    await flushPromises();

    expect(pushSpy).toHaveBeenCalledWith("/");
  });

  it("shows the invitation-only login message when signups are closed", async () => {
    const router = await createTestRouter("/login");
    axios.get.mockResolvedValueOnce({
      data: { invitation_only: true },
    });

    const wrapper = mountPage(LoginPage, router);
    await flushPromises();

    expect(wrapper.text()).toContain("Registration is invitation-only");
    expect(wrapper.text()).not.toContain("Sign up");
  });

  it("keeps working when the login page config or submit flow fails", async () => {
    const router = await createTestRouter("/login");
    const pushSpy = vi.spyOn(router, "push");
    axios.get.mockRejectedValueOnce(new Error("boom"));
    const authStore = useAuthStore();
    authStore.login = vi.fn().mockRejectedValue(new Error("bad credentials"));

    const wrapper = mountPage(LoginPage, router);
    await flushPromises();

    await wrapper
      .find('input[placeholder="Enter your username"]')
      .setValue("mai_lin");
    await wrapper
      .find('input[placeholder="Enter your password"]')
      .setValue("secret");
    await wrapper
      .findAll("button")
      .find((button) => button.text().includes("Login"))
      .trigger("click");
    await flushPromises();

    expect(pushSpy).not.toHaveBeenCalled();
  });

  it("stops login submission when validation fails", async () => {
    const router = await createTestRouter("/login");
    const authStore = useAuthStore();
    authStore.login = vi.fn();

    const wrapper = mountPage(LoginPage, router, {
      ElForm: InvalidElFormStub,
    });
    await flushPromises();

    await wrapper
      .findAll("button")
      .find((button) => button.text().includes("Login"))
      .trigger("click");
    await flushPromises();

    expect(authStore.login).not.toHaveBeenCalled();
  });

  it("keeps users on the login page when authentication fails", async () => {
    const router = await createTestRouter("/login");
    const pushSpy = vi.spyOn(router, "push");
    const authStore = useAuthStore();
    authStore.login = vi.fn().mockRejectedValue({
      response: {
        data: {
          detail: "Bad credentials",
        },
      },
    });

    const wrapper = mountPage(LoginPage, router);
    await flushPromises();
    await wrapper
      .find('input[placeholder="Enter your username"]')
      .setValue("mai_lin");
    await wrapper
      .find('input[placeholder="Enter your password"]')
      .setValue("secret");
    await wrapper
      .findAll("button")
      .find((button) => button.text().includes("Login"))
      .trigger("click");
    await flushPromises();

    expect(pushSpy).not.toHaveBeenCalledWith("/");
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

    await wrapper
      .find('input[placeholder="Choose a username"]')
      .setValue("ivy_ops");
    await wrapper
      .find('input[placeholder="your@email.com"]')
      .setValue("ivy@example.com");
    await wrapper
      .find('input[placeholder="Create a password"]')
      .setValue("securepass");
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

  it("registers invited users without a return URL and routes them home", async () => {
    const router = await createTestRouter("/register?invitation=invite-123");
    const pushSpy = vi.spyOn(router, "push");
    const authStore = useAuthStore();
    authStore.register = vi.fn().mockResolvedValue({
      message: "Registration successful",
      email_verified: true,
    });
    authStore.login = vi.fn().mockResolvedValue({ ok: true });

    const wrapper = mountPage(RegisterPage, router);
    await flushPromises();

    await wrapper
      .find('input[placeholder="Choose a username"]')
      .setValue("ivy_ops");
    await wrapper
      .find('input[placeholder="your@email.com"]')
      .setValue("ivy@example.com");
    await wrapper
      .find('input[placeholder="Create a password"]')
      .setValue("securepass");
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
    expect(pushSpy).toHaveBeenCalledWith("/");
  });

  it("returns to login when registration does not verify the email", async () => {
    const router = await createTestRouter("/register");
    const pushSpy = vi.spyOn(router, "push");
    const authStore = useAuthStore();
    authStore.register = vi.fn().mockResolvedValue({
      message: "Check your email",
      email_verified: false,
    });

    const wrapper = mountPage(RegisterPage, router);
    await flushPromises();

    await wrapper
      .find('input[placeholder="Choose a username"]')
      .setValue("noah_kim");
    await wrapper
      .find('input[placeholder="your@email.com"]')
      .setValue("noah@example.com");
    await wrapper
      .find('input[placeholder="Create a password"]')
      .setValue("securepass");
    await wrapper
      .findAll("button")
      .find((button) => button.text().includes("Sign Up"))
      .trigger("click");
    await flushPromises();

    expect(pushSpy).toHaveBeenCalledWith("/login");
  });

  it("routes verified registrations home when no return URL is set", async () => {
    const router = await createTestRouter("/register");
    const pushSpy = vi.spyOn(router, "push");
    const authStore = useAuthStore();
    authStore.register = vi.fn().mockResolvedValue({
      message: "Registration successful",
      email_verified: true,
    });
    authStore.login = vi.fn().mockResolvedValue({ ok: true });

    const wrapper = mountPage(RegisterPage, router);
    await flushPromises();

    await wrapper
      .find('input[placeholder="Choose a username"]')
      .setValue("ivy_ops");
    await wrapper
      .find('input[placeholder="your@email.com"]')
      .setValue("ivy@example.com");
    await wrapper
      .find('input[placeholder="Create a password"]')
      .setValue("securepass");
    await wrapper
      .findAll("button")
      .find((button) => button.text().includes("Sign Up"))
      .trigger("click");
    await flushPromises();

    expect(pushSpy).toHaveBeenCalledWith("/");
  });

  it("keeps the register page stable when submission fails", async () => {
    const router = await createTestRouter("/register");
    const pushSpy = vi.spyOn(router, "push");
    axios.get.mockRejectedValueOnce(new Error("boom"));
    const authStore = useAuthStore();
    authStore.register = vi.fn().mockRejectedValue(new Error("bad request"));

    const wrapper = mountPage(RegisterPage, router);
    await flushPromises();

    await wrapper
      .find('input[placeholder="Choose a username"]')
      .setValue("noah_kim");
    await wrapper
      .find('input[placeholder="your@email.com"]')
      .setValue("noah@example.com");
    await wrapper
      .find('input[placeholder="Create a password"]')
      .setValue("securepass");
    await wrapper
      .findAll("button")
      .find((button) => button.text().includes("Sign Up"))
      .trigger("click");
    await flushPromises();

    expect(pushSpy).not.toHaveBeenCalledWith("/");
    expect(pushSpy).not.toHaveBeenCalledWith("/login");
  });

  it("uses the default success message and skips invalid register submissions", async () => {
    const router = await createTestRouter("/register");
    const authStore = useAuthStore();
    authStore.register = vi.fn().mockResolvedValue({
      email_verified: false,
    });

    const invalidWrapper = mountPage(RegisterPage, router, {
      ElForm: InvalidElFormStub,
    });
    await flushPromises();
    await invalidWrapper
      .findAll("button")
      .find((button) => button.text().includes("Sign Up"))
      .trigger("click");
    await flushPromises();
    expect(authStore.register).not.toHaveBeenCalled();

    const validWrapper = mountPage(RegisterPage, router);
    await flushPromises();
    await validWrapper
      .find('input[placeholder="Choose a username"]')
      .setValue("ivy_ops");
    await validWrapper
      .find('input[placeholder="your@email.com"]')
      .setValue("ivy@example.com");
    await validWrapper
      .find('input[placeholder="Create a password"]')
      .setValue("securepass");
    await validWrapper
      .findAll("button")
      .find((button) => button.text().includes("Sign Up"))
      .trigger("click");
    await flushPromises();

    expect(authStore.register).toHaveBeenCalled();
  });

  it("keeps users on the register page when registration fails", async () => {
    const router = await createTestRouter("/register");
    const pushSpy = vi.spyOn(router, "push");
    const authStore = useAuthStore();
    authStore.register = vi.fn().mockRejectedValue({
      response: {
        data: {
          detail: "Registration failed",
        },
      },
    });

    const wrapper = mountPage(RegisterPage, router);
    await flushPromises();
    await wrapper
      .find('input[placeholder="Choose a username"]')
      .setValue("ivy_ops");
    await wrapper
      .find('input[placeholder="your@email.com"]')
      .setValue("ivy@example.com");
    await wrapper
      .find('input[placeholder="Create a password"]')
      .setValue("securepass");
    await wrapper
      .findAll("button")
      .find((button) => button.text().includes("Sign Up"))
      .trigger("click");
    await flushPromises();

    expect(pushSpy).not.toHaveBeenCalledWith("/");
    expect(pushSpy).not.toHaveBeenCalledWith("/login");
  });
});
