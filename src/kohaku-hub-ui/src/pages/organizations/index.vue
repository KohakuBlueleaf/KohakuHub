<!-- src/pages/organizations/index.vue -->
<template>
  <div class="container-main">
    <div class="flex items-center justify-between mb-6">
      <h1 class="text-3xl font-bold">Organizations</h1>
      <el-button
        v-if="authStore.isAuthenticated"
        type="primary"
        @click="router.push('/organizations/new')"
      >
        <div class="i-carbon-add mr-2" />
        Create Organization
      </el-button>
    </div>

    <!-- Search and Filters -->
    <div class="card mb-6">
      <el-input
        v-model="searchQuery"
        placeholder="Search organizations..."
        clearable
        class="max-w-md"
      >
        <template #prefix>
          <div class="i-carbon-search" />
        </template>
      </el-input>
    </div>

    <!-- Organizations Grid -->
    <div v-if="loading" class="text-center py-12">
      <el-skeleton :rows="5" animated />
    </div>

    <div v-else-if="filteredOrganizations.length > 0">
      <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        <div
          v-for="org in filteredOrganizations"
          :key="org.name"
          class="card hover:shadow-lg transition-shadow cursor-pointer"
          @click="goToOrganization(org.name)"
        >
          <div class="flex items-start gap-3 mb-3">
            <!-- Organization Avatar -->
            <img
              :src="`/api/organizations/${org.name}/avatar?t=${Date.now()}`"
              :alt="`${org.name} avatar`"
              class="w-16 h-16 rounded-full object-cover border-2 border-gray-200 dark:border-gray-600 flex-shrink-0"
              @error="
                (e) => {
                  e.target.style.display = 'none';
                  e.target.nextElementSibling.style.display = 'flex';
                }
              "
            />
            <div
              class="i-carbon-group text-4xl text-blue-500 flex-shrink-0"
              style="display: none"
            />
            <div class="flex-1 min-w-0">
              <h3 class="text-lg font-semibold truncate">
                <RouterLink
                  :to="getOrganizationPath(org.name)"
                  class="block text-blue-600 dark:text-blue-400 hover:underline"
                  @click.stop
                >
                  {{ org.name }}
                </RouterLink>
              </h3>
              <p
                class="text-sm text-gray-600 dark:text-gray-400 mt-1"
                style="
                  display: -webkit-box;
                  line-clamp: 2;
                  -webkit-line-clamp: 2;
                  -webkit-box-orient: vertical;
                  overflow: hidden;
                "
              >
                {{ org.description || "No description" }}
              </p>
            </div>
          </div>

          <div
            class="flex items-center gap-4 text-sm text-gray-500 dark:text-gray-400 mt-3 pt-3 border-t"
          >
            <div class="flex items-center gap-1">
              <div class="i-carbon-user-multiple" />
              <span>{{ org.memberCount || 0 }} members</span>
            </div>
            <div class="flex items-center gap-1">
              <div class="i-carbon-data-base" />
              <span>{{ org.repoCount || 0 }} repos</span>
            </div>
          </div>

          <div v-if="org.role" class="mt-2">
            <el-tag size="small" :type="getRoleType(org.role)">
              {{ org.role }}
            </el-tag>
          </div>
        </div>
      </div>
    </div>

    <div v-else class="text-center py-12">
      <div class="i-carbon-group text-6xl text-gray-400 mb-4 inline-block" />
      <p class="text-gray-500 dark:text-gray-400 mb-4">
        {{ searchQuery ? "No organizations found" : "No organizations yet" }}
      </p>
      <el-button
        v-if="authStore.isAuthenticated && !searchQuery"
        type="primary"
        @click="router.push('/organizations/new')"
      >
        Create Your First Organization
      </el-button>
    </div>
  </div>
</template>

<script setup>
import { useRouter } from "vue-router";
import { useAuthStore } from "@/stores/auth";
import { orgAPI, repoAPI, settingsAPI } from "@/utils/api";
import { ElMessage } from "element-plus";

const router = useRouter();
const authStore = useAuthStore();

const loading = ref(true);
const searchQuery = ref("");
const organizations = ref([]);

const filteredOrganizations = computed(() => {
  if (!searchQuery.value) {
    return organizations.value;
  }
  const query = searchQuery.value.toLowerCase();
  return organizations.value.filter(
    (org) =>
      org.name.toLowerCase().includes(query) ||
      (org.description && org.description.toLowerCase().includes(query)),
  );
});

function getRoleType(role) {
  switch (role) {
    case "super-admin":
      return "danger";
    case "admin":
      return "warning";
    case "member":
      return "info";
    default:
      return "";
  }
}

function getOrganizationPath(orgName) {
  return `/organizations/${orgName}`;
}

function goToOrganization(orgName) {
  router.push(getOrganizationPath(orgName));
}

async function loadOrganizations() {
  loading.value = true;
  try {
    // If user is authenticated, get their organizations with roles
    if (authStore.isAuthenticated) {
      const { data } = await settingsAPI.whoamiV2();
      const userOrgs = data.orgs || [];

      // Fetch detailed info for each organization
      const orgsWithDetails = await Promise.all(
        userOrgs.map(async (org) => {
          try {
            const [orgInfo, members, repos] = await Promise.all([
              orgAPI.get(org.name),
              orgAPI.listMembers(org.name),
              fetchOrgRepoCount(org.name),
            ]);

            return {
              name: org.name,
              description: orgInfo.data.description,
              role: org.roleInOrg || org.role,
              memberCount: members.data.members?.length || 0,
              repoCount: repos,
              created_at: orgInfo.data.created_at,
            };
          } catch (err) {
            console.error(`Failed to load details for ${org.name}:`, err);
            return {
              name: org.name,
              role: org.roleInOrg || org.role,
              description: "",
              memberCount: 0,
              repoCount: 0,
            };
          }
        }),
      );

      organizations.value = orgsWithDetails;
    } else {
      // For non-authenticated users, we could show public organizations
      // For now, show empty list
      organizations.value = [];
    }
  } catch (err) {
    console.error("Failed to load organizations:", err);
    ElMessage.error("Failed to load organizations");
  } finally {
    loading.value = false;
  }
}

async function fetchOrgRepoCount(orgName) {
  try {
    const [models, datasets, spaces] = await Promise.all([
      repoAPI.listRepos("model", { author: orgName, limit: 1000 }),
      repoAPI.listRepos("dataset", { author: orgName, limit: 1000 }),
      repoAPI.listRepos("space", { author: orgName, limit: 1000 }),
    ]);
    return (
      (models.data?.length || 0) +
      (datasets.data?.length || 0) +
      (spaces.data?.length || 0)
    );
  } catch (err) {
    return 0;
  }
}

onMounted(() => {
  loadOrganizations();
});
</script>
