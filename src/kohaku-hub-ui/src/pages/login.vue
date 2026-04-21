<!-- src/kohaku-hub-ui/src/pages/login.vue -->
<template>
  <div class="min-h-[calc(100vh-16rem)] flex items-center justify-center">
    <div class="card w-full max-w-md">
      <h1 class="text-2xl font-bold mb-6 text-center">Login to KohakuHub</h1>

      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-position="top"
        @submit.prevent="handleSubmit"
      >
        <el-form-item label="Username" prop="username">
          <el-input
            v-model="form.username"
            placeholder="Enter your username"
            size="large"
            @keyup.enter="handleSubmit"
          />
        </el-form-item>

        <el-form-item label="Password" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            placeholder="Enter your password"
            size="large"
            show-password
            @keyup.enter="handleSubmit"
          />
        </el-form-item>

        <el-button
          type="primary"
          size="large"
          class="w-full"
          :loading="loading"
          @click="handleSubmit"
        >
          Login
        </el-button>
      </el-form>

      <div
        v-if="!siteConfig?.invitation_only"
        class="mt-4 text-center text-sm text-gray-600 dark:text-gray-400"
      >
        Don't have an account?
        <RouterLink
          to="/register"
          class="text-blue-500 dark:text-blue-400 hover:underline"
        >
          Sign up
        </RouterLink>
      </div>

      <!-- Invitation-only message -->
      <div
        v-else
        class="mt-4 text-center text-sm text-gray-600 dark:text-gray-400"
      >
        <div class="i-carbon-locked inline-block mr-1" />
        Registration is invitation-only. Contact the administrator if you don't
        have an account.
      </div>
    </div>
  </div>
</template>

<script setup>
import { useAuthStore } from "@/stores/auth";
import { useRoute, useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import axios from "axios";

const route = useRoute();
const router = useRouter();
const authStore = useAuthStore();
const formRef = ref(null);
const loading = ref(false);
const siteConfig = ref(null);

const form = reactive({
  username: "",
  password: "",
});

const rules = {
  username: [
    { required: true, message: "Please enter username", trigger: "blur" },
  ],
  password: [
    { required: true, message: "Please enter password", trigger: "blur" },
  ],
};

async function loadSiteConfig() {
  try {
    const { data } = await axios.get("/api/site-config");
    siteConfig.value = data;
  } catch (err) {
    console.error("Failed to load site config:", err);
  }
}

async function handleSubmit() {
  if (!formRef.value) return;

  await formRef.value.validate(async (valid) => {
    if (!valid) return;

    loading.value = true;
    try {
      await authStore.login(form);
      ElMessage.success("Login successful");

      // Check for return URL query parameter
      const returnUrl = route.query.return;
      if (returnUrl) {
        router.push(decodeURIComponent(returnUrl));
      } else {
        router.push("/");
      }
    } catch (err) {
      ElMessage.error(err.response?.data?.detail || "Login failed");
    } finally {
      loading.value = false;
    }
  });
}

onMounted(() => {
  loadSiteConfig();
});
</script>
