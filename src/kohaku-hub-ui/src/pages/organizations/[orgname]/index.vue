<!-- src/pages/organizations/[orgname]/index.vue -->
<template>
  <div class="container-main">
    <!-- Loading State -->
    <div v-if="loading" class="text-center py-20">
      <div
        class="inline-block animate-spin rounded-full h-16 w-16 border-4 border-gray-300 border-t-blue-600 mb-4"
      ></div>
      <p class="text-xl text-gray-600 dark:text-gray-400">
        Loading organization...
      </p>
    </div>

    <!-- Organization Not Found -->
    <div v-else-if="orgNotFound" class="text-center py-20">
      <div
        class="i-carbon-group text-8xl text-gray-300 dark:text-gray-600 mb-6 inline-block"
      />
      <h1 class="text-4xl font-bold mb-4">Organization Not Found</h1>
      <p class="text-xl text-gray-600 dark:text-gray-400 mb-8">
        The organization "<span
          class="font-mono text-blue-600 dark:text-blue-400"
          >{{ orgname }}</span
        >" does not exist.
      </p>
      <el-button type="primary" @click="$router.push('/')">
        <div class="i-carbon-home mr-2" />
        Go to Homepage
      </el-button>
    </div>

    <div v-else>
      <!-- Header with Settings Button (only for local admins) -->
      <div
        v-if="isAdmin && !isExternalOrg"
        class="flex items-center justify-end mb-4"
      >
        <el-button
          type="primary"
          size="small"
          @click="$router.push(`/organizations/${orgname}/settings`)"
        >
          <div class="i-carbon-settings mr-1" />
          Organization Settings
        </el-button>
      </div>

      <div class="grid grid-cols-1 lg:grid-cols-[280px_1fr] gap-6">
        <!-- Sidebar with members -->
        <aside class="space-y-4 lg:sticky lg:top-20 lg:self-start">
          <div class="card">
            <div class="flex items-center gap-3 mb-4">
              <!-- Avatar (always try API endpoint with fallback) -->
              <img
                v-if="hasAvatar"
                :src="`/api/organizations/${orgname}/avatar?t=${Date.now()}`"
                :alt="`${orgname} avatar`"
                class="w-20 h-20 rounded-full object-cover"
                @error="hasAvatar = false"
              />
              <div v-else class="i-carbon-group text-5xl text-gray-400" />

              <div>
                <h2 class="text-xl font-bold">{{ orgname }}</h2>
                <p class="text-sm text-gray-600 dark:text-gray-400">
                  Organization
                </p>
                <!-- External Source Badge -->
                <el-tag
                  v-if="isExternalOrg"
                  size="small"
                  type="info"
                  class="mt-1"
                >
                  <div class="i-carbon-cloud inline-block mr-1" />
                  {{ externalSourceName }}
                </el-tag>
              </div>
            </div>

            <div v-if="profileInfo" class="space-y-3 text-sm">
              <!-- Description -->
              <p
                v-if="profileInfo.description"
                class="text-gray-700 dark:text-gray-300"
              >
                {{ profileInfo.description }}
              </p>

              <!-- Bio -->
              <p
                v-if="profileInfo.bio"
                class="text-gray-700 dark:text-gray-300 whitespace-pre-wrap"
              >
                {{ profileInfo.bio }}
              </p>

              <!-- Links Section (Website + Social Media) -->
              <div
                v-if="profileInfo.website || profileInfo.social_media"
                class="flex gap-3"
              >
                <!-- Icons Column (10% width) -->
                <div class="flex flex-col gap-2 w-10% min-w-8">
                  <!-- Website Icon -->
                  <div
                    v-if="profileInfo.website"
                    class="flex items-center justify-center h-6"
                  >
                    <div
                      class="i-carbon-link w-4 h-4 text-gray-600 dark:text-gray-400"
                    />
                  </div>

                  <!-- Social Media Icons -->
                  <div
                    v-if="profileInfo.social_media?.twitter_x"
                    class="flex items-center justify-center h-6"
                  >
                    <div
                      class="i-carbon-logo-x w-4 h-4 text-gray-600 dark:text-gray-400"
                    />
                  </div>
                  <div
                    v-if="profileInfo.social_media?.threads"
                    class="flex items-center justify-center h-6"
                  >
                    <div
                      class="i-carbon-logo-instagram w-4 h-4 text-gray-600 dark:text-gray-400"
                    />
                  </div>
                  <div
                    v-if="profileInfo.social_media?.github"
                    class="flex items-center justify-center h-6"
                  >
                    <div
                      class="i-carbon-logo-github w-4 h-4 text-gray-600 dark:text-gray-400"
                    />
                  </div>
                  <div
                    v-if="profileInfo.social_media?.huggingface"
                    class="flex items-center justify-center h-6"
                  >
                    <span class="text-base">🤗</span>
                  </div>
                </div>

                <!-- Links Column (90% width) -->
                <div class="flex flex-col gap-2 flex-1">
                  <!-- Website Link -->
                  <a
                    v-if="profileInfo.website"
                    :href="profileInfo.website"
                    target="_blank"
                    rel="noopener noreferrer"
                    class="flex items-center h-6 text-sm text-blue-600 dark:text-blue-400 hover:underline transition-colors truncate"
                    title="Website"
                  >
                    {{ profileInfo.website.replace(/^https?:\/\//, "") }}
                  </a>

                  <!-- Social Media Links -->
                  <a
                    v-if="profileInfo.social_media?.twitter_x"
                    :href="`https://twitter.com/${profileInfo.social_media.twitter_x}`"
                    target="_blank"
                    rel="noopener noreferrer"
                    class="flex items-center h-6 text-sm text-gray-700 dark:text-gray-300 hover:text-blue-600 dark:hover:text-blue-400 transition-colors truncate"
                    title="Twitter/X"
                  >
                    @{{ profileInfo.social_media.twitter_x }}
                  </a>

                  <a
                    v-if="profileInfo.social_media?.threads"
                    :href="`https://www.threads.net/@${profileInfo.social_media.threads}`"
                    target="_blank"
                    rel="noopener noreferrer"
                    class="flex items-center h-6 text-sm text-gray-700 dark:text-gray-300 hover:text-purple-600 dark:hover:text-purple-400 transition-colors truncate"
                    title="Threads"
                  >
                    @{{ profileInfo.social_media.threads }}
                  </a>

                  <a
                    v-if="profileInfo.social_media?.github"
                    :href="`https://github.com/${profileInfo.social_media.github}`"
                    target="_blank"
                    rel="noopener noreferrer"
                    class="flex items-center h-6 text-sm text-gray-700 dark:text-gray-300 hover:text-gray-900 dark:hover:text-white transition-colors truncate"
                    title="GitHub"
                  >
                    {{ profileInfo.social_media.github }}
                  </a>

                  <a
                    v-if="profileInfo.social_media?.huggingface"
                    :href="`https://huggingface.co/${profileInfo.social_media.huggingface}`"
                    target="_blank"
                    rel="noopener noreferrer"
                    class="flex items-center h-6 text-sm text-gray-700 dark:text-gray-300 hover:text-yellow-600 dark:hover:text-yellow-400 transition-colors truncate"
                    title="HuggingFace"
                  >
                    {{ profileInfo.social_media.huggingface }}
                  </a>
                </div>
              </div>

              <!-- Created Date -->
              <div
                class="flex items-center gap-2 text-gray-600 dark:text-gray-400 pt-2 border-t"
              >
                <div class="i-carbon-calendar" />
                Created {{ formatDate(profileInfo.created_at) }}
              </div>

              <!-- Member Count -->
              <div
                class="flex items-center gap-2 text-gray-600 dark:text-gray-400"
              >
                <div class="i-carbon-user-multiple" />
                {{ profileInfo.member_count || members.length }} member{{
                  (profileInfo.member_count || members.length) !== 1 ? "s" : ""
                }}
              </div>
            </div>
          </div>

          <!-- Members List -->
          <div class="card">
            <h3 class="font-semibold mb-3 flex items-center gap-2">
              <div class="i-carbon-user-multiple" />
              Members
              <span class="text-sm text-gray-500"
                >({{ members.length || 0 }})</span
              >
            </h3>
            <div v-if="members.length > 0" class="space-y-2">
              <div
                v-for="member in members"
                :key="member.user"
                class="flex items-center gap-2 p-2 rounded hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors cursor-pointer"
                @click="goToUser(member.user)"
              >
                <!-- Member Avatar -->
                <img
                  :src="`/api/users/${member.user}/avatar?t=${Date.now()}`"
                  :alt="`${member.user} avatar`"
                  class="w-8 h-8 rounded-full object-cover border border-gray-300 dark:border-gray-600"
                  @error="
                    (e) => {
                      e.target.style.display = 'none';
                      e.target.nextElementSibling.style.display = 'block';
                    }
                  "
                />
                <div
                  class="i-carbon-user-avatar text-2xl text-gray-400"
                  style="display: none"
                />
                <div class="flex-1 min-w-0">
                  <div class="text-sm font-medium truncate">
                    {{ member.user }}
                  </div>
                  <div class="text-xs text-gray-500">
                    {{ member.role }}
                  </div>
                </div>
              </div>
            </div>
            <div
              v-else
              class="text-center py-4 text-sm text-gray-500 dark:text-gray-400"
            >
              No members yet
            </div>
          </div>

          <!-- Storage Quota Card -->
          <div v-if="quotaInfo" class="card">
            <h3 class="font-semibold mb-3 flex items-center gap-2">
              <div class="i-carbon-data-base text-gray-500" />
              Storage Usage
            </h3>

            <!-- Public Storage -->
            <div class="mb-4">
              <div class="flex justify-between items-center mb-1">
                <span class="text-sm text-gray-600 dark:text-gray-400"
                  >Public</span
                >
                <span class="text-sm font-mono">
                  {{ formatBytes(quotaInfo.public_used_bytes) }}
                  <span
                    v-if="quotaInfo.public_quota_bytes !== null"
                    class="text-gray-400"
                  >
                    / {{ formatBytes(quotaInfo.public_quota_bytes) }}
                  </span>
                  <span v-else class="text-gray-400">/ Unlimited</span>
                </span>
              </div>
              <el-progress
                v-if="quotaInfo.public_quota_bytes !== null"
                :percentage="
                  Math.min(100, quotaInfo.public_percentage_used || 0)
                "
                :status="getQuotaStatus(quotaInfo.public_percentage_used)"
                :show-text="false"
              />
              <div
                v-else
                class="h-1.5 bg-gray-200 dark:bg-gray-700 rounded-full"
              ></div>
            </div>

            <!-- Private Storage (only if user has permission) -->
            <div v-if="quotaInfo.can_see_private" class="mb-2">
              <div class="flex justify-between items-center mb-1">
                <span class="text-sm text-gray-600 dark:text-gray-400"
                  >Private</span
                >
                <span class="text-sm font-mono">
                  {{ formatBytes(quotaInfo.private_used_bytes) }}
                  <span
                    v-if="quotaInfo.private_quota_bytes !== null"
                    class="text-gray-400"
                  >
                    / {{ formatBytes(quotaInfo.private_quota_bytes) }}
                  </span>
                  <span v-else class="text-gray-400">/ Unlimited</span>
                </span>
              </div>
              <el-progress
                v-if="quotaInfo.private_quota_bytes !== null"
                :percentage="
                  Math.min(100, quotaInfo.private_percentage_used || 0)
                "
                :status="getQuotaStatus(quotaInfo.private_percentage_used)"
                :show-text="false"
              />
              <div
                v-else
                class="h-1.5 bg-gray-200 dark:bg-gray-700 rounded-full"
              ></div>
            </div>

            <!-- Total -->
            <div class="pt-2 border-t border-gray-200 dark:border-gray-700">
              <div class="flex justify-between items-center">
                <span class="text-sm font-semibold">Total</span>
                <span class="text-sm font-mono font-semibold">
                  {{ formatBytes(quotaInfo.total_used_bytes) }}
                </span>
              </div>
            </div>

            <!-- View Details Button (only for members with write permission) -->
            <div v-if="quotaInfo.can_see_private" class="mt-4">
              <el-button
                size="small"
                @click="$router.push(`/organizations/${orgname}/storage`)"
                class="w-full"
              >
                <div class="i-carbon-chart-bar inline-block mr-1" />
                View Details
              </el-button>
            </div>
          </div>

          <!-- Stats Summary / Tab Navigation -->
          <div class="card">
            <h3 class="font-semibold mb-3">Repositories</h3>
            <div class="space-y-1">
              <RouterLink
                :to="`/organizations/${orgname}`"
                :class="[
                  'flex items-center justify-between px-3 py-2 rounded cursor-pointer transition-colors block',
                  'bg-gray-100 dark:bg-gray-700',
                ]"
              >
                <div class="flex items-center gap-2 text-sm">
                  <div class="i-carbon-grid text-gray-500 dark:text-gray-400" />
                  <span class="font-semibold">Overview</span>
                </div>
              </RouterLink>

              <RouterLink
                :to="`/organizations/${orgname}/models`"
                :class="[
                  'flex items-center justify-between px-3 py-2 rounded cursor-pointer transition-colors block',
                  'hover:bg-gray-100 dark:hover:bg-gray-700',
                ]"
              >
                <div class="flex items-center gap-2 text-sm">
                  <div class="i-carbon-model text-blue-500" />
                  <span>Models</span>
                </div>
                <span
                  class="text-sm font-semibold text-gray-600 dark:text-gray-400"
                >
                  {{ getCount("model") }}
                </span>
              </RouterLink>

              <RouterLink
                :to="`/organizations/${orgname}/datasets`"
                :class="[
                  'flex items-center justify-between px-3 py-2 rounded cursor-pointer transition-colors block',
                  'hover:bg-gray-100 dark:hover:bg-gray-700',
                ]"
              >
                <div class="flex items-center gap-2 text-sm">
                  <div class="i-carbon-data-table text-green-500" />
                  <span>Datasets</span>
                </div>
                <span
                  class="text-sm font-semibold text-gray-600 dark:text-gray-400"
                >
                  {{ getCount("dataset") }}
                </span>
              </RouterLink>

              <RouterLink
                :to="`/organizations/${orgname}/spaces`"
                :class="[
                  'flex items-center justify-between px-3 py-2 rounded cursor-pointer transition-colors block',
                  'hover:bg-gray-100 dark:hover:bg-gray-700',
                ]"
              >
                <div class="flex items-center gap-2 text-sm">
                  <div class="i-carbon-application text-purple-500" />
                  <span>Spaces</span>
                </div>
                <span
                  class="text-sm font-semibold text-gray-600 dark:text-gray-400"
                >
                  {{ getCount("space") }}
                </span>
              </RouterLink>
            </div>
          </div>
        </aside>

        <!-- Main Content -->
        <main class="space-y-8">
          <!-- Organization Card (from OrgName/OrgName space repo card if exists) -->
          <section v-if="orgCard" class="card">
            <div class="markdown-body">
              <MarkdownViewer :content="orgCard" />
            </div>
          </section>

          <!-- Models Section -->
          <section class="mb-8">
            <div
              class="flex items-center justify-between gap-3 flex-wrap mb-4 pb-3 border-b-2 border-blue-500"
            >
              <div class="flex items-center gap-2">
                <div class="i-carbon-model text-blue-500 text-xl md:text-2xl" />
                <h2 class="text-xl md:text-2xl font-bold">Models</h2>
              </div>
              <div class="flex items-center gap-3 ml-auto shrink-0">
                <div class="w-56 sm:w-64 lg:w-72 shrink-0">
                  <el-select
                    v-model="selectedSorts.model"
                    placeholder="Sort repositories"
                    class="w-full"
                  >
                    <el-option
                      v-for="option in sortOptions"
                      :key="option.value"
                      :label="option.label"
                      :value="option.value"
                    />
                  </el-select>
                </div>
                <el-tag type="info" size="large">{{ getCount("model") }}</el-tag>
              </div>
            </div>

            <div v-if="getCount('model') > 0" class="space-y-4">
              <!-- Grid of repos (2 per row, max 3 rows = 6 repos) -->
              <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div
                  v-for="repo in displayedRepos('model')"
                  :key="repo.id"
                  class="card hover:shadow-md transition-shadow cursor-pointer"
                  @click="goToRepo('model', repo)"
                >
                  <div class="flex items-start gap-2 mb-2">
                    <div
                      class="i-carbon-model text-blue-500 text-xl flex-shrink-0"
                    />
                    <div class="flex-1 min-w-0">
                      <h3 class="font-semibold">
                        <RouterLink
                          :to="getRepoPath('model', repo)"
                          class="block text-blue-600 dark:text-blue-400 hover:underline truncate"
                          @click.stop
                        >
                          {{ repo.id }}
                        </RouterLink>
                      </h3>
                      <div
                        class="text-xs text-gray-600 dark:text-gray-400 mt-1"
                      >
                        Updated {{ formatDate(repo.lastModified) }}
                      </div>
                    </div>
                  </div>

                  <div
                    v-if="repo.tags && repo.tags.length"
                    class="flex gap-1 mb-2 flex-wrap"
                  >
                    <el-tag
                      v-for="tag in repo.tags.slice(0, 2)"
                      :key="tag"
                      size="small"
                      effect="plain"
                    >
                      {{ tag }}
                    </el-tag>
                  </div>

                  <!-- External Source Badge + Link -->
                  <div
                    v-if="repo._source && repo._source !== 'local'"
                    class="mb-2"
                  >
                    <el-button
                      size="small"
                      type="primary"
                      plain
                      @click.stop="openExternalRepo(repo, 'model')"
                    >
                      <div class="i-carbon-launch inline-block mr-1" />
                      View on {{ repo._source }}
                    </el-button>
                  </div>

                  <div
                    class="flex items-center gap-3 text-xs text-gray-500 dark:text-gray-400"
                  >
                    <div class="flex items-center gap-1">
                      <div class="i-carbon-download" />
                      {{ repo.downloads || 0 }}
                    </div>
                    <div class="flex items-center gap-1">
                      <div class="i-carbon-favorite" />
                      {{ repo.likes || 0 }}
                    </div>
                  </div>
                </div>
              </div>

              <!-- Show More button -->
              <RouterLink :to="`/organizations/${orgname}/models`">
                <el-button v-if="hasMoreRepos('model')" class="w-full">
                  Show all {{ getCount("model") }} models ->
                </el-button>
              </RouterLink>
            </div>

            <div
              v-else
              class="text-center py-12 text-gray-500 dark:text-gray-400"
            >
              <div class="i-carbon-document-blank text-6xl mb-4 inline-block" />
              <p>No models yet</p>
            </div>
          </section>

          <!-- Datasets Section -->
          <section class="mb-8">
            <div
              class="flex items-center justify-between gap-3 flex-wrap mb-4 pb-3 border-b-2 border-green-500"
            >
              <div class="flex items-center gap-2">
                <div
                  class="i-carbon-data-table text-green-500 text-xl md:text-2xl"
                />
                <h2 class="text-xl md:text-2xl font-bold">Datasets</h2>
              </div>
              <div class="flex items-center gap-3 ml-auto shrink-0">
                <div class="w-56 sm:w-64 lg:w-72 shrink-0">
                  <el-select
                    v-model="selectedSorts.dataset"
                    placeholder="Sort repositories"
                    class="w-full"
                  >
                    <el-option
                      v-for="option in sortOptions"
                      :key="option.value"
                      :label="option.label"
                      :value="option.value"
                    />
                  </el-select>
                </div>
                <el-tag type="success" size="large">{{
                  getCount("dataset")
                }}</el-tag>
              </div>
            </div>

            <div v-if="getCount('dataset') > 0" class="space-y-4">
              <!-- Grid of repos (2 per row, max 3 rows = 6 repos) -->
              <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div
                  v-for="repo in displayedRepos('dataset')"
                  :key="repo.id"
                  class="card hover:shadow-md transition-shadow cursor-pointer"
                  @click="goToRepo('dataset', repo)"
                >
                  <div class="flex items-start gap-2 mb-2">
                    <div
                      class="i-carbon-data-table text-green-500 text-xl flex-shrink-0"
                    />
                    <div class="flex-1 min-w-0">
                      <h3 class="font-semibold">
                        <RouterLink
                          :to="getRepoPath('dataset', repo)"
                          class="block text-green-600 dark:text-green-400 hover:underline truncate"
                          @click.stop
                        >
                          {{ repo.id }}
                        </RouterLink>
                      </h3>
                      <div class="text-xs text-gray-600 mt-1">
                        Updated {{ formatDate(repo.lastModified) }}
                      </div>
                    </div>
                  </div>

                  <div
                    v-if="repo.tags && repo.tags.length"
                    class="flex gap-1 mb-2 flex-wrap"
                  >
                    <el-tag
                      v-for="tag in repo.tags.slice(0, 2)"
                      :key="tag"
                      size="small"
                      effect="plain"
                    >
                      {{ tag }}
                    </el-tag>
                  </div>

                  <!-- External Source Badge + Link -->
                  <div
                    v-if="repo._source && repo._source !== 'local'"
                    class="mb-2"
                  >
                    <el-button
                      size="small"
                      type="primary"
                      plain
                      @click.stop="openExternalRepo(repo, 'dataset')"
                    >
                      <div class="i-carbon-launch inline-block mr-1" />
                      View on {{ repo._source }}
                    </el-button>
                  </div>

                  <div
                    class="flex items-center gap-3 text-xs text-gray-500 dark:text-gray-400"
                  >
                    <div class="flex items-center gap-1">
                      <div class="i-carbon-download" />
                      {{ repo.downloads || 0 }}
                    </div>
                    <div class="flex items-center gap-1">
                      <div class="i-carbon-favorite" />
                      {{ repo.likes || 0 }}
                    </div>
                  </div>
                </div>
              </div>

              <!-- Show More button -->
              <RouterLink :to="`/organizations/${orgname}/datasets`">
                <el-button v-if="hasMoreRepos('dataset')" class="w-full">
                  Show all {{ getCount("dataset") }} datasets ->
                </el-button>
              </RouterLink>
            </div>

            <div
              v-else
              class="text-center py-12 text-gray-500 dark:text-gray-400"
            >
              <div class="i-carbon-document-blank text-6xl mb-4 inline-block" />
              <p>No datasets yet</p>
            </div>
          </section>

          <!-- Spaces Section -->
          <section>
            <div
              class="flex items-center justify-between gap-3 flex-wrap mb-4 pb-3 border-b-2 border-purple-500"
            >
              <div class="flex items-center gap-2">
                <div
                  class="i-carbon-application text-purple-500 text-xl md:text-2xl"
                />
                <h2 class="text-xl md:text-2xl font-bold">Spaces</h2>
              </div>
              <div class="flex items-center gap-3 ml-auto shrink-0">
                <div class="w-56 sm:w-64 lg:w-72 shrink-0">
                  <el-select
                    v-model="selectedSorts.space"
                    placeholder="Sort repositories"
                    class="w-full"
                  >
                    <el-option
                      v-for="option in sortOptions"
                      :key="option.value"
                      :label="option.label"
                      :value="option.value"
                    />
                  </el-select>
                </div>
                <el-tag type="warning" size="large">{{
                  getCount("space")
                }}</el-tag>
              </div>
            </div>

            <div v-if="getCount('space') > 0" class="space-y-4">
              <!-- Grid of repos (2 per row, max 3 rows = 6 repos) -->
              <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div
                  v-for="repo in displayedRepos('space')"
                  :key="repo.id"
                  class="card hover:shadow-md transition-shadow cursor-pointer"
                  @click="goToRepo('space', repo)"
                >
                  <div class="flex items-start gap-2 mb-2">
                    <div
                      class="i-carbon-application text-purple-500 text-xl flex-shrink-0"
                    />
                    <div class="flex-1 min-w-0">
                      <h3 class="font-semibold">
                        <RouterLink
                          :to="getRepoPath('space', repo)"
                          class="block text-purple-600 dark:text-purple-400 hover:underline truncate"
                          @click.stop
                        >
                          {{ repo.id }}
                        </RouterLink>
                      </h3>
                      <div class="text-xs text-gray-600 mt-1">
                        Updated {{ formatDate(repo.lastModified) }}
                      </div>
                    </div>
                  </div>

                  <div
                    v-if="repo.tags && repo.tags.length"
                    class="flex gap-1 mb-2 flex-wrap"
                  >
                    <el-tag
                      v-for="tag in repo.tags.slice(0, 2)"
                      :key="tag"
                      size="small"
                      effect="plain"
                    >
                      {{ tag }}
                    </el-tag>
                  </div>

                  <!-- External Source Badge + Link -->
                  <div
                    v-if="repo._source && repo._source !== 'local'"
                    class="mb-2"
                  >
                    <el-button
                      size="small"
                      type="primary"
                      plain
                      @click.stop="openExternalRepo(repo, 'space')"
                    >
                      <div class="i-carbon-launch inline-block mr-1" />
                      View on {{ repo._source }}
                    </el-button>
                  </div>

                  <div
                    class="flex items-center gap-3 text-xs text-gray-500 dark:text-gray-400"
                  >
                    <div class="flex items-center gap-1">
                      <div class="i-carbon-download" />
                      {{ repo.downloads || 0 }}
                    </div>
                    <div class="flex items-center gap-1">
                      <div class="i-carbon-favorite" />
                      {{ repo.likes || 0 }}
                    </div>
                  </div>
                </div>
              </div>

              <!-- Show More button -->
              <RouterLink :to="`/organizations/${orgname}/spaces`">
                <el-button v-if="hasMoreRepos('space')" class="w-full">
                  Show all {{ getCount("space") }} spaces ->
                </el-button>
              </RouterLink>
            </div>

            <div
              v-else
              class="text-center py-12 text-gray-500 dark:text-gray-400"
            >
              <div class="i-carbon-document-blank text-6xl mb-4 inline-block" />
              <p>No spaces yet</p>
            </div>
          </section>
        </main>
      </div>
    </div>
  </div>
</template>

<script setup>
import { repoAPI, orgAPI, settingsAPI } from "@/utils/api";
import MarkdownViewer from "@/components/common/MarkdownViewer.vue";
import SocialLinks from "@/components/profile/SocialLinks.vue";
import { formatRelativeTime } from "@/utils/datetime";
import {
  getRepoSortPreference,
  setRepoSortPreference,
} from "@/utils/repoSortPreference";
import axios from "axios";

const route = useRoute();
const router = useRouter();
const orgname = computed(() => route.params.orgname);

const loading = ref(true);
const orgInfo = ref(null);
const profileInfo = ref(null);
const members = ref([]);
const repos = ref({ model: [], dataset: [], space: [] });
const orgCard = ref("");
const quotaInfo = ref(null);
const userRole = ref(null);
const hasAvatar = ref(true); // Assume avatar exists, will be set to false on error
const orgNotFound = ref(false);
const selectedSorts = reactive({
  model: getRepoSortPreference({
    scope: "org",
    repoType: "model",
    allowedValues: ["recent", "updated", "downloads", "likes"],
    fallback: "recent",
  }),
  dataset: getRepoSortPreference({
    scope: "org",
    repoType: "dataset",
    allowedValues: ["recent", "updated", "downloads", "likes"],
    fallback: "recent",
  }),
  space: getRepoSortPreference({
    scope: "org",
    repoType: "space",
    allowedValues: ["recent", "updated", "downloads", "likes"],
    fallback: "recent",
  }),
});

const MAX_DISPLAYED = 6; // 2 per row × 3 rows

const sortOptions = [
  { label: "Recently Created", value: "recent" },
  { label: "Recently Updated", value: "updated" },
  { label: "Most Downloads", value: "downloads" },
  { label: "Most Likes", value: "likes" },
];

// External org detection
const isExternalOrg = computed(() => {
  return orgInfo.value?._source && orgInfo.value._source !== "local";
});

const externalSourceName = computed(() => {
  return orgInfo.value?._source || "external source";
});

const externalSourceUrl = computed(() => {
  return orgInfo.value?._source_url || "";
});

const hasPartialProfile = computed(() => {
  return orgInfo.value?._partial === true;
});

const externalAvatarUrl = computed(() => {
  return orgInfo.value?._avatar_url;
});

const isAdmin = computed(() => {
  return userRole.value && ["admin", "super-admin"].includes(userRole.value);
});

function getCount(type) {
  return repos.value[type]?.length || 0;
}

function displayedRepos(type) {
  return (repos.value[type] || []).slice(0, MAX_DISPLAYED);
}

function hasMoreRepos(type) {
  return getCount(type) > MAX_DISPLAYED;
}

function formatDate(date) {
  return formatRelativeTime(date, "never");
}

function formatBytes(bytes) {
  if (bytes === null || bytes === undefined) return "Unlimited";
  if (bytes === 0) return "0 B";
  const k = 1000;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
}

function getQuotaStatus(percentage) {
  if (percentage === null || percentage === undefined) return "";
  if (percentage >= 90) return "exception";
  if (percentage >= 75) return "warning";
  return "success";
}

function getRepoPath(type, repo) {
  const [namespace, name] = repo.id.split("/");
  return `/${type}s/${namespace}/${name}`;
}

function goToRepo(type, repo) {
  router.push(getRepoPath(type, repo));
}

function goToUser(username) {
  router.push(`/${username}`);
}

function openExternalRepo(repo, type) {
  if (!repo._source_url) return;

  // Check if source is HuggingFace
  const isHF =
    repo._source &&
    (repo._source.toLowerCase().includes("huggingface") ||
      repo._source_url.includes("huggingface.co"));

  let url;
  if (isHF) {
    // HuggingFace URLs: models have no prefix, datasets and spaces have prefix
    if (type === "model") {
      url = `${repo._source_url}/${repo.id}`;
    } else {
      url = `${repo._source_url}/${type}s/${repo.id}`;
    }
  } else {
    // KohakuHub and other sources: always use type prefix
    url = `${repo._source_url}/${type}s/${repo.id}`;
  }

  window.open(url, "_blank");
}

async function loadOrgInfo() {
  try {
    // Check org exists (WITH fallback to check all sources)
    const { data } = await axios.get(`/org/${orgname.value}`, {
      params: { fallback: true },
    });
    orgInfo.value = data;
    return true;
  } catch (err) {
    if (err.response?.status === 404) {
      // Not found in local or any fallback source
      orgNotFound.value = true;
      return false;
    }
    console.error("Failed to load org info:", err);
    return true; // Continue on non-404 errors
  }
}

async function loadMembers() {
  try {
    const { data } = await orgAPI.listMembers(orgname.value);
    members.value = data.members || [];
  } catch (err) {
    console.error("Failed to load members:", err);
  }
}

async function loadRepos() {
  try {
    const [models, datasets, spaces] = await Promise.all([
      loadRepoType("model"),
      loadRepoType("dataset"),
      loadRepoType("space"),
    ]);

    repos.value = {
      model: models,
      dataset: datasets,
      space: spaces,
    };

    return true;
  } catch (err) {
    console.error("Failed to load repos:", err);
    repos.value = { model: [], dataset: [], space: [] };
    return true; // Continue on error
  }
}

async function loadRepoType(repoType) {
  const { data } = await repoAPI.listRepos(repoType, {
    author: orgname.value,
    limit: 100000,
    sort: selectedSorts[repoType],
  });
  return data;
}

watch(
  () => selectedSorts.model,
  async () => {
    setRepoSortPreference({
      scope: "org",
      repoType: "model",
      value: selectedSorts.model,
    });

    if (!loading.value && !orgNotFound.value) {
      repos.value.model = await loadRepoType("model");
    }
  },
);

watch(
  () => selectedSorts.dataset,
  async () => {
    setRepoSortPreference({
      scope: "org",
      repoType: "dataset",
      value: selectedSorts.dataset,
    });

    if (!loading.value && !orgNotFound.value) {
      repos.value.dataset = await loadRepoType("dataset");
    }
  },
);

watch(
  () => selectedSorts.space,
  async () => {
    setRepoSortPreference({
      scope: "org",
      repoType: "space",
      value: selectedSorts.space,
    });

    if (!loading.value && !orgNotFound.value) {
      repos.value.space = await loadRepoType("space");
    }
  },
);

async function loadOrgCard() {
  try {
    // Try to fetch README from OrgName/OrgName space repo
    const url = `/spaces/${orgname.value}/${orgname.value}/resolve/main/README.md`;
    const response = await axios.get(url);
    orgCard.value = response.data;
  } catch (err) {
    // No org card available - this is fine
    orgCard.value = "";
  }
}

async function loadQuotaInfo() {
  try {
    const response = await axios.get(`/api/quota/${orgname.value}/public`, {
      withCredentials: true,
    });
    quotaInfo.value = response.data;
  } catch (err) {
    console.error("Failed to load quota info:", err);
    // Don't show error to user - quota info is optional
    quotaInfo.value = null;
  }
}

async function loadProfileInfo() {
  try {
    const { data } = await settingsAPI.getOrgProfile(orgname.value);
    profileInfo.value = data;
  } catch (err) {
    console.error("Failed to load profile info:", err);
    // Profile info is optional
    profileInfo.value = null;
  }
}

async function checkUserRole() {
  try {
    const { data } = await settingsAPI.whoamiV2();
    const org = data.orgs?.find((o) => o.name === orgname.value);
    userRole.value = org?.roleInOrg || org?.role || null;
  } catch (err) {
    // Not logged in or error - user is not a member
    userRole.value = null;
  }
}

onMounted(async () => {
  try {
    loading.value = true;

    // Load org info first to check if it exists (with fallback)
    const orgExists = await loadOrgInfo();
    if (!orgExists) {
      // Organization not found - stop loading other data
      return;
    }

    // Organization exists - load repos and other data
    await loadRepos();

    // Load other data in parallel
    checkUserRole();
    loadProfileInfo();
    loadMembers();
    loadOrgCard();
    loadQuotaInfo();
  } finally {
    loading.value = false;
  }
});
</script>
