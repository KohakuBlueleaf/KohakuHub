<script setup>
import { ref } from "vue";
import { useRouter, useRoute } from "vue-router";
import { useAdminStore } from "@/stores/admin";
import { useThemeStore } from "@/stores/theme";
import { ElMessage } from "element-plus";
import GlobalSearch from "@/components/GlobalSearch.vue";

const router = useRouter();
const route = useRoute();
const adminStore = useAdminStore();
const themeStore = useThemeStore();

const globalSearchRef = ref(null);

function handleLogout() {
  adminStore.logout();
  ElMessage.success("Logged out successfully");
  router.push("/login");
}

function openGlobalSearch() {
  if (globalSearchRef.value) {
    globalSearchRef.value.openDialog();
  }
}

const menuItems = [
  { path: "/", label: "Dashboard", icon: "i-carbon-dashboard" },
  { path: "/users", label: "Users", icon: "i-carbon-user-multiple" },
  { path: "/invitations", label: "Invitations", icon: "i-carbon-email" },
  { path: "/repositories", label: "Repositories", icon: "i-carbon-data-base" },
  { path: "/commits", label: "Commits", icon: "i-carbon-version" },
  { path: "/storage", label: "Storage", icon: "i-carbon-data-volume" },
  {
    path: "/fallback-sources",
    label: "Fallback Sources",
    icon: "i-carbon-connect",
  },
  {
    path: "/QuotaOverview",
    label: "Quota Overview",
    icon: "i-carbon-meter",
  },
  {
    path: "/health",
    label: "Health",
    icon: "i-carbon-activity",
  },
  {
    path: "/cache",
    label: "Cache",
    icon: "i-carbon-data-vis-1",
  },
  {
    path: "/credentials",
    label: "Credentials",
    icon: "i-carbon-password",
  },
  {
    path: "/DatabaseViewer",
    label: "Database",
    icon: "i-carbon-data-table",
  },
];
</script>

<template>
  <el-container class="admin-layout">
    <!-- Sidebar -->
    <el-aside width="250px" class="sidebar">
      <div class="sidebar-header">
        <div
          class="i-carbon-security text-2xl text-blue-600 dark:text-blue-400"
        />
        <h2 class="text-xl font-bold ml-2 text-gray-900 dark:text-gray-100">
          Admin Portal
        </h2>
      </div>

      <el-menu
        :default-active="route.path"
        router
        class="sidebar-menu"
        :background-color="themeStore.isDark ? '#1f1f1f' : '#ffffff'"
        :text-color="themeStore.isDark ? '#e0e0e0' : '#303133'"
        :active-text-color="'#409EFF'"
      >
        <el-menu-item
          v-for="item in menuItems"
          :key="item.path"
          :index="item.path"
        >
          <div :class="item.icon" class="mr-2" />
          <span>{{ item.label }}</span>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <!-- Main Content -->
    <el-container>
      <!-- Header -->
      <el-header class="header">
        <div class="header-title">
          <h1 class="text-xl font-semibold text-gray-900 dark:text-gray-100">
            KohakuHub Administration
          </h1>
        </div>

        <div class="header-actions">
          <el-button @click="openGlobalSearch" class="search-button">
            <div class="i-carbon-search text-lg" />
            <span class="ml-2 hidden sm:inline">Search</span>
            <el-tag size="small" effect="plain" class="ml-2 hidden md:inline">
              Ctrl+K
            </el-tag>
          </el-button>
          <el-button circle @click="themeStore.toggle()" class="mr-2">
            <div v-if="themeStore.isDark" class="i-carbon-moon text-lg" />
            <div v-else class="i-carbon-asleep text-lg" />
          </el-button>
          <el-button type="danger" @click="handleLogout" :icon="'SwitchButton'">
            Logout
          </el-button>
        </div>
      </el-header>

      <!-- Content -->
      <el-main class="main-content">
        <slot />
      </el-main>
    </el-container>

    <!-- Global Search Modal -->
    <GlobalSearch ref="globalSearchRef" />
  </el-container>
</template>

<style scoped>
.admin-layout {
  min-height: 100vh;
  background-color: var(--bg-base);
}

.sidebar {
  background-color: var(--bg-elevated);
  border-right: 1px solid var(--border-default);
  box-shadow: var(--shadow-sm);
  transition: all 0.2s ease;
}

.sidebar-header {
  display: flex;
  align-items: center;
  padding: 24px 20px;
  border-bottom: 1px solid var(--border-light);
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
}

.sidebar-header h2 {
  color: white !important;
}

.sidebar-menu {
  border-right: none;
  background-color: var(--bg-elevated) !important;
}

.header {
  background-color: var(--bg-elevated);
  border-bottom: 1px solid var(--border-default);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  box-shadow: var(--shadow-sm);
  transition: all 0.2s ease;
}

.header-title {
  flex: 1;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.search-button {
  border-color: var(--border-default);
  background-color: var(--bg-hover);
  transition: all 0.2s ease;
}

.search-button:hover {
  border-color: var(--color-info);
  background-color: var(--bg-active);
}

.main-content {
  background-color: var(--bg-base);
  min-height: calc(100vh - 60px);
}
</style>
