<!-- src/components/repo/RepoViewer.vue -->
<template>
  <div class="container-main">
    <el-breadcrumb separator="/" class="mb-6 text-gray-700 dark:text-gray-300">
      <el-breadcrumb-item :to="{ path: '/' }">Home</el-breadcrumb-item>
      <el-breadcrumb-item :to="{ path: `/${repoType}s` }">
        {{ repoTypeLabel }}
      </el-breadcrumb-item>
      <el-breadcrumb-item :to="{ path: namespaceLink }">
        {{ namespace }}
      </el-breadcrumb-item>
      <el-breadcrumb-item>{{ name }}</el-breadcrumb-item>
    </el-breadcrumb>

    <div v-if="loading" class="text-center py-20">
      <el-icon class="is-loading" :size="40">
        <div class="i-carbon-loading" />
      </el-icon>
    </div>

    <div v-else-if="error" class="text-center py-20">
      <div class="i-carbon-warning text-6xl text-red-500 mb-4" />
      <h2 class="text-2xl font-bold mb-2">Repository Not Found</h2>
      <p class="text-gray-600 mb-4">{{ error }}</p>
      <el-button @click="$router.back()">Go Back</el-button>
    </div>

    <div
      v-else
      :class="
        activeTab === 'viewer'
          ? ''
          : 'grid grid-cols-1 lg:grid-cols-[1fr_300px] gap-6'
      "
    >
      <!-- Main Content -->
      <main class="min-w-0">
        <!-- Repo Header (hidden for viewer tab) -->
        <div v-if="activeTab !== 'viewer'" class="card mb-6">
          <div
            class="flex flex-col sm:flex-row items-start justify-between gap-4 mb-4"
          >
            <div class="flex items-start gap-3">
              <div
                :class="getIconClass(repoType)"
                class="text-3xl sm:text-4xl flex-shrink-0"
              />
              <div class="min-w-0">
                <h1
                  class="text-xl sm:text-2xl lg:text-3xl font-bold break-words"
                >
                  {{ repoInfo?.id }}
                </h1>
                <div class="flex items-center gap-2 mt-1">
                  <RouterLink
                    :to="namespaceLink"
                    class="text-blue-600 dark:text-blue-400 hover:underline"
                  >
                    {{ namespace }}
                  </RouterLink>
                  <span class="text-gray-400 dark:text-gray-500">/</span>
                  <span class="text-gray-700 dark:text-gray-300">{{
                    name
                  }}</span>
                  <button
                    @click="copyRepoId"
                    class="ml-1 p-1 hover:bg-gray-100 dark:hover:bg-gray-800 rounded transition-colors"
                    title="Copy repository ID"
                  >
                    <div
                      class="i-carbon-copy text-sm text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
                    />
                  </button>
                </div>
              </div>
            </div>

            <div class="flex items-center gap-2 flex-wrap">
              <el-tag v-if="repoInfo?.private" type="warning">
                <div class="i-carbon-locked inline-block mr-1" />
                Private
              </el-tag>
              <el-tag v-else type="success">
                <div class="i-carbon-unlocked inline-block mr-1" />
                Public
              </el-tag>
              <el-tag v-if="isExternalRepo" type="info">
                <div class="i-carbon-cloud inline-block mr-1" />
                {{ repoInfo._source }}
              </el-tag>
              <el-button
                v-if="isExternalRepo && repoInfo._source_url"
                size="small"
                type="primary"
                plain
                @click="openExternalRepo"
              >
                <div class="i-carbon-launch inline-block mr-1" />
                View on {{ repoInfo._source }}
              </el-button>
            </div>
          </div>

          <!-- Stats -->
          <div
            class="flex flex-wrap items-center gap-3 sm:gap-6 text-xs sm:text-sm text-gray-600 dark:text-gray-400"
          >
            <div class="flex items-center gap-1">
              <div class="i-carbon-download" />
              <span>{{ repoInfo?.downloads || 0 }} downloads</span>
            </div>
            <button
              v-if="authStore.isAuthenticated"
              @click="toggleLike"
              :class="[
                'flex items-center gap-1 transition-all hover:scale-105',
                isLiked
                  ? 'text-red-500 dark:text-red-400'
                  : 'text-gray-600 dark:text-gray-400 hover:text-red-500 dark:hover:text-red-400',
              ]"
              :disabled="likingInProgress"
            >
              <div
                :class="
                  isLiked ? 'i-carbon-favorite-filled' : 'i-carbon-favorite'
                "
              />
              <span>{{ likesCount }}</span>
            </button>
            <div v-else class="flex items-center gap-1">
              <div class="i-carbon-favorite" />
              <span>{{ likesCount }}</span>
            </div>
            <div class="flex items-center gap-1">
              <div class="i-carbon-calendar" />
              <span>Updated {{ formatDate(repoInfo?.lastModified) }}</span>
            </div>
          </div>

          <!-- Actions -->
          <div class="flex flex-col sm:flex-row gap-1 mt-4">
            <el-button
              type="primary"
              @click="showCloneDialog = true"
              size="small"
              class="w-full sm:w-auto m-0 sm:m-1"
            >
              <div class="i-carbon-download inline-block mr-1" />
              Clone
            </el-button>
            <div class="w-0 h-0 p-0 m-0"></div>
            <el-button
              @click="downloadRepo"
              size="small"
              class="w-full sm:w-auto m-0 sm:m-1"
              plain
            >
              <div class="i-carbon-document-download inline-block mr-1" />
              Download
            </el-button>
            <div class="w-0 h-0 p-0 m-0"></div>
            <el-button
              v-if="isOwner"
              @click="navigateToSettings"
              size="small"
              class="w-full sm:w-auto m-0 sm:m-1"
              plain
            >
              <div class="i-carbon-settings inline-block mr-1" />
              Settings
            </el-button>
          </div>
        </div>

        <!-- Metadata Header (Key badges) (hidden for viewer tab) -->
        <MetadataHeader
          v-if="hasMetadataHeader && activeTab !== 'viewer'"
          :metadata="readmeMetadata"
          :repo-type="repoType"
          @navigate-to-metadata="navigateToTab('metadata')"
        />

        <!-- Navigation Tabs -->
        <div class="mb-6 -mx-4 sm:mx-0 px-4 sm:px-0 overflow-x-auto">
          <div
            class="flex gap-1 border-b border-gray-200 dark:border-gray-700 min-w-max sm:min-w-0"
          >
            <button
              :class="[
                'px-4 py-2 font-medium transition-colors',
                activeTab === 'card'
                  ? 'border-b-2 border-blue-500 text-blue-600 dark:text-blue-400'
                  : 'text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200',
              ]"
              @click="navigateToTab('card')"
            >
              {{
                repoType === "model"
                  ? "Model Card"
                  : repoType === "dataset"
                    ? "Dataset Card"
                    : "App"
              }}
            </button>
            <button
              :class="[
                'px-4 py-2 font-medium transition-colors',
                activeTab === 'files'
                  ? 'border-b-2 border-blue-500 text-blue-600 dark:text-blue-400'
                  : 'text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200',
              ]"
              @click="navigateToTab('files')"
            >
              Files
            </button>
            <button
              :class="[
                'px-4 py-2 font-medium transition-colors',
                activeTab === 'commits'
                  ? 'border-b-2 border-blue-500 text-blue-600 dark:text-blue-400'
                  : 'text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200',
                isExternalRepo ? 'opacity-50 cursor-not-allowed' : '',
              ]"
              @click="!isExternalRepo && navigateToTab('commits')"
              :disabled="isExternalRepo"
              :title="
                isExternalRepo
                  ? `Commits not available for ${externalSourceName} repos`
                  : ''
              "
            >
              Commits
            </button>
            <button
              :class="[
                'px-4 py-2 font-medium transition-colors',
                activeTab === 'metadata'
                  ? 'border-b-2 border-blue-500 text-blue-600 dark:text-blue-400'
                  : 'text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200',
              ]"
              @click="navigateToTab('metadata')"
            >
              Metadata
            </button>
            <button
              v-if="repoType === 'dataset'"
              :class="[
                'px-4 py-2 font-medium transition-colors',
                activeTab === 'viewer'
                  ? 'border-b-2 border-blue-500 text-blue-600 dark:text-blue-400'
                  : 'text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200',
              ]"
              @click="navigateToTab('viewer')"
            >
              <div class="i-carbon-data-table inline-block mr-1" />
              Viewer
            </button>
          </div>
        </div>

        <!-- Tab Content -->
        <div v-if="activeTab === 'card'" class="card overflow-hidden">
          <div class="max-w-full overflow-x-auto">
            <div v-if="readmeLoading" class="text-center py-12">
              <el-icon class="is-loading" :size="40">
                <div class="i-carbon-loading" />
              </el-icon>
              <p class="mt-4 text-gray-500 dark:text-gray-400">
                Loading README...
              </p>
            </div>
            <div v-else-if="readmeContent">
              <MarkdownViewer
                :content="readmeContent"
                :repo-type="repoType"
                :namespace="namespace"
                :name="name"
                :branch="currentBranch"
              />
            </div>
            <div
              v-else
              class="text-center py-12 text-gray-500 dark:text-gray-400"
            >
              <div class="i-carbon-document-blank text-6xl mb-4 inline-block" />
              <p>No README.md found</p>
              <el-button
                v-if="isOwner"
                class="mt-4"
                type="primary"
                @click="createReadme"
              >
                Create README.md
              </el-button>
            </div>
          </div>
        </div>

        <!-- Metadata Tab -->
        <div v-if="activeTab === 'metadata'">
          <DetailedMetadataPanel
            v-if="hasDetailedMetadata"
            :metadata="readmeMetadata"
            :repo-type="repoType"
          />
          <div
            v-else
            class="card text-center py-12 text-gray-500 dark:text-gray-400"
          >
            <div class="i-carbon-information text-6xl mb-4 inline-block" />
            <p>No metadata found in README.md</p>
            <p class="text-sm mt-2">
              Add YAML frontmatter to README.md to display metadata
            </p>
          </div>
        </div>

        <!-- Viewer Tab (for datasets only) -->
        <div v-if="activeTab === 'viewer' && repoType === 'dataset'">
          <DatasetViewerTab
            :repo-type="repoType"
            :namespace="namespace"
            :name="name"
            :branch="currentBranch"
            :files="fileTree"
          />
        </div>

        <div v-if="activeTab === 'files'" class="card">
          <div
            class="mb-4 flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3"
          >
            <div class="flex items-center gap-2">
              <el-select
                v-model="currentBranch"
                size="small"
                class="w-full sm:w-37"
                @change="handleBranchChange"
              >
                <el-option label="main" value="main" />
              </el-select>
              <span
                class="text-sm text-gray-600 dark:text-gray-400 whitespace-nowrap"
              >
                {{ fileTree.length }}
                {{ fileTree.length === 1 ? "file" : "files" }}
              </span>
            </div>

            <div
              class="flex flex-col sm:flex-row items-stretch sm:items-center gap-2"
            >
              <el-button
                v-if="isOwner"
                size="small"
                type="primary"
                @click="navigateToUpload"
                class="w-full sm:w-auto"
              >
                <div class="i-carbon-cloud-upload inline-block mr-1" />
                Upload Files
              </el-button>
              <el-input
                v-model="fileSearchQuery"
                placeholder="Search files..."
                size="small"
                class="w-full sm:w-50"
                clearable
              >
                <template #prefix>
                  <div class="i-carbon-search" />
                </template>
              </el-input>
            </div>
          </div>

          <!-- Breadcrumb for current path -->
          <div v-if="currentPath" class="mb-3">
            <div class="flex items-center justify-between">
              <el-breadcrumb
                separator="/"
                class="text-sm text-gray-700 dark:text-gray-300"
              >
                <el-breadcrumb-item>
                  <RouterLink
                    :to="`/${repoType}s/${namespace}/${name}/tree/${currentBranch}`"
                    class="text-blue-600 dark:text-blue-400 hover:underline"
                  >
                    root
                  </RouterLink>
                </el-breadcrumb-item>
                <el-breadcrumb-item
                  v-for="(segment, idx) in pathSegments"
                  :key="idx"
                >
                  <RouterLink
                    :to="`/${repoType}s/${namespace}/${name}/tree/${currentBranch}/${pathSegments.slice(0, idx + 1).join('/')}`"
                    class="text-blue-600 dark:text-blue-400 hover:underline"
                  >
                    {{ segment }}
                  </RouterLink>
                </el-breadcrumb-item>
              </el-breadcrumb>
              <el-button
                v-if="isOwner"
                @click="confirmDeleteFolder"
                type="danger"
                size="small"
                :loading="deletingFolder"
              >
                <div class="i-carbon-trash-can inline-block mr-1" />
                Delete Folder
              </el-button>
            </div>
          </div>

          <!-- File List -->
          <div class="divide-y divide-gray-200 dark:divide-gray-700">
            <div v-if="filesLoading" class="py-12 text-center">
              <el-icon class="is-loading" :size="40">
                <div class="i-carbon-loading" />
              </el-icon>
              <p class="mt-4 text-gray-500 dark:text-gray-400">
                Loading files...
              </p>
            </div>
            <template v-else>
              <!-- Header Row (desktop only) -->
              <div
                class="hidden md:grid md:grid-cols-[auto_1fr_120px_150px] gap-3 py-2 px-2 text-sm font-medium text-gray-600 dark:text-gray-400 border-b"
              >
                <div></div>
                <!-- Icon column -->
                <div>Name</div>
                <div class="text-right">Size</div>
                <div class="text-right">Last Modified</div>
              </div>

              <!-- File Rows -->
              <div
                v-for="file in filteredFiles"
                :key="file.path"
                class="py-3 grid grid-cols-[auto_1fr_auto] md:grid-cols-[auto_1fr_120px_150px] gap-3 items-center hover:bg-gray-50 dark:hover:bg-gray-700 px-2 cursor-pointer transition-colors"
                @click="handleFileClick(file)"
              >
                <div
                  :class="
                    file.type === 'directory'
                      ? 'i-carbon-folder text-blue-500'
                      : 'i-carbon-document text-gray-500 dark:text-gray-400'
                  "
                  class="text-xl flex-shrink-0"
                />
                <div class="min-w-0">
                  <div class="font-medium truncate">
                    {{ getFileName(file.path) }}
                  </div>
                </div>
                <div
                  class="text-sm text-gray-500 dark:text-gray-400 text-right"
                >
                  {{ formatSize(file.size) }}
                </div>
                <div
                  class="hidden md:block text-sm text-gray-500 dark:text-gray-400 text-right"
                >
                  {{ formatLastModified(file.lastModified) }}
                </div>
              </div>

              <div
                v-if="filteredFiles.length === 0"
                class="py-12 text-center text-gray-500 dark:text-gray-400"
              >
                <div
                  class="i-carbon-document-blank text-6xl mb-4 inline-block"
                />
                <p>No files found</p>
              </div>
            </template>
          </div>
        </div>

        <div v-if="activeTab === 'commits'">
          <!-- External Repo Warning -->
          <div v-if="isExternalRepo" class="card text-center py-12">
            <div
              class="i-carbon-warning text-6xl text-yellow-500 dark:text-yellow-400 mb-4 inline-block"
            />
            <h3
              class="text-xl font-semibold text-gray-900 dark:text-white mb-2"
            >
              Commits Not Available
            </h3>
            <p class="text-gray-600 dark:text-gray-400 mb-4">
              This repository is from {{ externalSourceName }}. Commit history
              is not available for external repositories.
            </p>
            <p class="text-sm text-gray-500 dark:text-gray-500">
              Visit the source repository to view commits.
            </p>
          </div>

          <!-- Local Repo Commits -->
          <div v-else class="card">
            <h2 class="text-xl font-semibold mb-4">Commit History</h2>

            <div
              v-if="commitsLoading && commits.length === 0"
              class="text-center py-12"
            >
              <el-icon class="is-loading" :size="40">
                <div class="i-carbon-renew" />
              </el-icon>
              <p class="mt-4 text-gray-500 dark:text-gray-400">
                Loading commits...
              </p>
            </div>

            <div v-else-if="commits.length > 0" class="space-y-3">
              <div
                v-for="commit in commits"
                :key="commit.id"
                class="border border-gray-200 dark:border-gray-700 rounded-lg p-4 hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors cursor-pointer"
                @click="viewCommit(commit.id)"
              >
                <div class="flex items-start gap-3">
                  <div
                    class="i-carbon-commit text-2xl text-blue-500 flex-shrink-0 mt-1"
                  />
                  <div class="flex-1 min-w-0">
                    <div
                      class="font-medium text-sm mb-1 hover:text-blue-600 dark:hover:text-blue-400 transition-colors"
                    >
                      {{ commit.title }}
                    </div>
                    <div
                      class="flex items-center gap-3 text-xs text-gray-600 dark:text-gray-400"
                    >
                      <div class="flex items-center gap-1">
                        <div class="i-carbon-user-avatar" />
                        <RouterLink
                          :to="`/${commit.author}`"
                          class="text-blue-600 dark:text-blue-400 hover:underline"
                          @click.stop
                        >
                          {{ commit.author }}
                        </RouterLink>
                      </div>
                      <div class="flex items-center gap-1">
                        <div class="i-carbon-calendar" />
                        <span>{{ formatCommitDate(commit.date) }}</span>
                      </div>
                      <div class="font-mono text-xs">
                        {{ commit.id.slice(0, 7) }}
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              <!-- Load More Button -->
              <div v-if="commitsHasMore" class="text-center pt-4">
                <el-button
                  @click="loadMoreCommits"
                  :loading="commitsLoading"
                  plain
                >
                  Load More Commits
                </el-button>
              </div>
            </div>

            <div
              v-else
              class="text-center py-12 text-gray-500 dark:text-gray-400"
            >
              <div class="i-carbon-branch text-6xl mb-4 inline-block" />
              <p>No commits yet</p>
            </div>
          </div>
        </div>
      </main>

      <!-- Sidebar (Compact) -->
      <aside
        v-if="activeTab !== 'viewer'"
        class="space-y-4 lg:sticky lg:top-20 lg:self-start"
      >
        <!-- Relationships (Author + Base Model + Datasets from YAML) -->
        <SidebarRelationshipsCard
          :namespace="namespace"
          :namespace-link="namespaceLink"
          :metadata="readmeMetadata"
          :repo-type="repoType"
        />

        <!-- Basic Metadata -->
        <div class="card">
          <h3 class="font-semibold mb-3">Info</h3>
          <div class="space-y-2 text-sm">
            <div>
              <span class="text-gray-600 dark:text-gray-400">Type:</span>
              <span class="ml-2 font-medium">{{ repoTypeLabel }}</span>
            </div>
            <div>
              <span class="text-gray-600 dark:text-gray-400">Created:</span>
              <span class="ml-2">{{ formatDate(repoInfo?.createdAt) }}</span>
            </div>
            <div v-if="repoInfo?.lastModified">
              <span class="text-gray-600 dark:text-gray-400">Updated:</span>
              <span class="ml-2">{{ formatDate(repoInfo?.lastModified) }}</span>
            </div>
            <div v-if="repoInfo?.sha">
              <span class="text-gray-600 dark:text-gray-400">Commit:</span>
              <span class="ml-2 font-mono text-xs">{{
                repoInfo.sha.slice(0, 7)
              }}</span>
            </div>
          </div>
        </div>

        <!-- Storage -->
        <div v-if="repoInfo?.storage" class="card">
          <h3 class="font-semibold mb-3">Storage</h3>
          <div class="space-y-3 text-sm">
            <div>
              <div class="flex items-center justify-between mb-1">
                <span class="text-gray-600 dark:text-gray-400">Usage:</span>
                <span class="font-medium">{{
                  formatSize(repoInfo.storage.used_bytes)
                }}</span>
              </div>
              <div
                v-if="repoInfo.storage.effective_quota_bytes"
                class="flex items-center justify-between text-xs text-gray-500 dark:text-gray-400"
              >
                <span>Limit:</span>
                <span>{{
                  formatSize(repoInfo.storage.effective_quota_bytes)
                }}</span>
              </div>
              <div
                v-if="
                  repoInfo.storage.percentage_used !== null &&
                  repoInfo.storage.percentage_used !== undefined
                "
                class="mt-2"
              >
                <el-progress
                  :percentage="
                    Math.min(
                      100,
                      Math.round(repoInfo.storage.percentage_used * 100) / 100,
                    )
                  "
                  :color="getProgressColor(repoInfo.storage.percentage_used)"
                  :stroke-width="6"
                  :format="(percentage) => `${percentage.toFixed(2)}%`"
                />
              </div>
              <div
                v-if="repoInfo.storage.is_inheriting"
                class="mt-2 text-xs text-gray-500 dark:text-gray-400"
              >
                <div class="i-carbon-information inline-block mr-1" />
                Inheriting from {{ namespace }} quota
              </div>
            </div>
          </div>
        </div>
      </aside>
    </div>

    <!-- Clone Dialog -->
    <el-dialog v-model="showCloneDialog" title="Clone Repository" width="700px">
      <div class="space-y-4">
        <!-- Git Clone Section -->
        <div>
          <label class="block text-sm font-medium mb-2 flex items-center gap-2">
            <div class="i-carbon-code text-lg" />
            Clone with Git (Recommended)
          </label>
          <el-input :value="gitCloneUrl" readonly class="font-mono text-sm">
            <template #append>
              <el-button @click="copyGitCloneUrl">
                <div class="i-carbon-copy" />
              </el-button>
            </template>
          </el-input>

          <div
            class="bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 p-4 rounded mt-3 text-sm"
          >
            <p class="font-medium text-blue-900 dark:text-blue-100 mb-2">
              <span class="i-carbon-terminal inline-block mr-1" />
              Quick Start:
            </p>
            <pre
              class="text-xs overflow-x-auto bg-white dark:bg-gray-800 p-2 rounded mb-2 text-gray-800 dark:text-gray-200"
            >
git clone {{ gitCloneUrl }}</pre
            >
            <p class="text-blue-800 dark:text-blue-200 text-xs mt-2">
              This will clone the repository with all files and commit history.
            </p>
          </div>
        </div>

        <!-- Authentication Section (only show for private repos or authenticated users) -->
        <div
          v-if="repoInfo?.private || authStore.isLoggedIn"
          class="bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-800 p-4 rounded text-sm"
        >
          <p class="font-medium text-yellow-900 dark:text-yellow-100 mb-2">
            <span class="i-carbon-locked inline-block mr-1" />
            Authentication Required
          </p>
          <p class="text-yellow-800 dark:text-yellow-200 text-xs mb-2">
            For
            {{
              repoInfo?.private ? "this private repository" : "push operations"
            }}, you'll need to authenticate using an access token:
          </p>
          <ol
            class="list-decimal list-inside space-y-1 text-xs text-yellow-900 dark:text-yellow-100 ml-2"
          >
            <li>Generate an access token in your settings</li>
            <li>Use your username and token when prompted</li>
            <li>
              Username:
              <code class="bg-white dark:bg-gray-800 px-1 py-0.5 rounded">{{
                authStore.user?.username || "your-username"
              }}</code>
            </li>
            <li>
              Password:
              <code class="bg-white dark:bg-gray-800 px-1 py-0.5 rounded"
                >your-access-token</code
              >
            </li>
          </ol>
        </div>

        <!-- HuggingFace CLI Alternative -->
        <div>
          <label class="block text-sm font-medium mb-2 flex items-center gap-2">
            <div class="i-carbon-download text-lg" />
            Alternative: HuggingFace CLI
          </label>
          <div
            class="bg-gray-50 dark:bg-gray-900 p-4 rounded text-sm border border-gray-200 dark:border-gray-700"
          >
            <p class="mb-2 text-gray-600 dark:text-gray-400">
              Set the endpoint:
            </p>
            <pre
              class="text-xs overflow-x-auto bg-white dark:bg-gray-800 p-2 rounded mb-3 text-gray-800 dark:text-gray-200"
            >
export HF_ENDPOINT={{ baseUrl }}</pre
            >
            <p class="text-gray-600 dark:text-gray-400 mb-1">
              Download using
              <code class="bg-white dark:bg-gray-800 px-1 py-0.5 rounded"
                >huggingface-cli</code
              >:
            </p>
            <pre
              class="text-xs overflow-x-auto bg-white dark:bg-gray-800 p-2 rounded text-gray-800 dark:text-gray-200"
            >
huggingface-cli download {{ repoInfo?.id }}</pre
            >
          </div>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ElMessage, ElMessageBox } from "element-plus";
import axios from "axios";
import dayjs from "dayjs";
import relativeTime from "dayjs/plugin/relativeTime";

import { useAuthStore } from "@/stores/auth";
import { copyToClipboard } from "@/utils/clipboard";
import { parseYAMLFrontmatter, normalizeMetadata } from "@/utils/yaml-parser";
import { parseTags } from "@/utils/tag-parser";
import { likesAPI, repoAPI } from "@/utils/api";
import MarkdownViewer from "@/components/common/MarkdownViewer.vue";
import MetadataHeader from "@/components/repo/metadata/MetadataHeader.vue";
import DetailedMetadataPanel from "@/components/repo/metadata/DetailedMetadataPanel.vue";
import ReferencedDatasetsCard from "@/components/repo/metadata/ReferencedDatasetsCard.vue";
import SidebarRelationshipsCard from "@/components/repo/metadata/SidebarRelationshipsCard.vue";
import DatasetViewerTab from "@/components/repo/DatasetViewerTab.vue";

dayjs.extend(relativeTime);

/**
 * @typedef {Object} Props
 * @property {string} repoType - Repository type (model/dataset/space)
 * @property {string} namespace - Repository namespace
 * @property {string} name - Repository name
 * @property {string} [branch] - Current branch
 * @property {string} [currentPath] - Current folder path
 * @property {string} [tab] - Active tab (card/files/commits)
 */
const props = defineProps({
  repoType: { type: String, required: true },
  namespace: { type: String, required: true },
  name: { type: String, required: true },
  branch: { type: String, default: "main" },
  currentPath: { type: String, default: "" },
  tab: { type: String, default: "card" },
});

const router = useRouter();
const authStore = useAuthStore();

// State
const loading = ref(true);
const error = ref(null);
const repoInfo = ref(null);
const currentBranch = ref(props.branch);
const fileTree = ref([]);
const commits = ref([]);
const commitsLoading = ref(false);
const commitsHasMore = ref(false);
const commitsNextCursor = ref(null);
const filesLoading = ref(true);
const readmeContent = ref("");
const readmeLoading = ref(true);
const readmeMetadata = ref({});
const showCloneDialog = ref(false);
const fileSearchQuery = ref("");
const isLiked = ref(false);
const likesCount = ref(0);
const likingInProgress = ref(false);
const deletingFolder = ref(false);

const baseUrl = window.location.origin;

// Computed
const activeTab = computed(() => props.tab);

const repoTypeLabel = computed(() => {
  const labels = { model: "Models", dataset: "Datasets", space: "Spaces" };
  return labels[props.repoType] || "Models";
});

const isOwner = computed(() => {
  return authStore.canWriteToNamespace(props.namespace);
});

const isNamespaceOrg = ref(false);

const namespaceLink = computed(() => {
  if (isNamespaceOrg.value) {
    return `/organizations/${props.namespace}`;
  }
  return `/${props.namespace}`;
});

const cloneUrl = computed(() => {
  return `${baseUrl}/${repoInfo.value?.id}.git`;
});

const gitCloneUrl = computed(() => {
  return `${baseUrl}/${props.namespace}/${props.name}.git`;
});

const pathSegments = computed(() => {
  return props.currentPath ? props.currentPath.split("/").filter(Boolean) : [];
});

const filteredFiles = computed(() => {
  // Backend now provides folder stats, so just filter
  if (!fileSearchQuery.value) return fileTree.value;

  const query = fileSearchQuery.value.toLowerCase();
  return fileTree.value.filter((file) =>
    getFileName(file.path).toLowerCase().includes(query),
  );
});

// Parse tags from repo info
const parsedTags = computed(() => {
  return parseTags(repoInfo.value?.tags || []);
});

const referencedDatasets = computed(() => {
  return parsedTags.value.datasets;
});

const cleanTags = computed(() => {
  return parsedTags.value.cleanTags;
});

const showTagsCard = computed(() => {
  return cleanTags.value.length > 0;
});

const showReferencedDatasetsCard = computed(() => {
  return referencedDatasets.value.length > 0;
});

// Metadata visibility
const hasMetadataHeader = computed(() => {
  return (
    readmeMetadata.value.license ||
    readmeMetadata.value.language ||
    readmeMetadata.value.library_name ||
    readmeMetadata.value.pipeline_tag ||
    readmeMetadata.value.task_categories ||
    readmeMetadata.value.size_categories
  );
});

const hasDetailedMetadata = computed(() => {
  return Object.keys(readmeMetadata.value).length > 0;
});

// Check if repo is from external source
const isExternalRepo = computed(() => {
  return repoInfo.value?._source && repoInfo.value._source !== "local";
});

const externalSourceName = computed(() => {
  return repoInfo.value?._source || "external source";
});

// Methods
function getIconClass(type) {
  const icons = {
    model: "i-carbon-model text-blue-500",
    dataset: "i-carbon-data-table text-green-500",
    space: "i-carbon-application text-purple-500",
  };
  return icons[type] || icons.model;
}

function openExternalRepo() {
  if (!repoInfo.value?._source_url) return;

  // Check if source is HuggingFace
  const isHF =
    repoInfo.value._source &&
    (repoInfo.value._source.toLowerCase().includes("huggingface") ||
      repoInfo.value._source_url.includes("huggingface.co"));

  let url;
  if (isHF) {
    // HuggingFace URLs: models have no prefix, datasets and spaces have prefix
    if (props.repoType === "model") {
      url = `${repoInfo.value._source_url}/${props.namespace}/${props.name}`;
    } else {
      url = `${repoInfo.value._source_url}/${props.repoType}s/${props.namespace}/${props.name}`;
    }
  } else {
    // KohakuHub and other sources: always use type prefix
    url = `${repoInfo.value._source_url}/${props.repoType}s/${props.namespace}/${props.name}`;
  }

  window.open(url, "_blank");
}

function formatDate(date) {
  return date ? dayjs(date).fromNow() : "Unknown";
}

function formatSize(bytes) {
  if (!bytes || bytes === 0) return "-";
  if (bytes < 1000) return bytes + " B";
  if (bytes < 1000 * 1000) return (bytes / 1000).toFixed(1) + " KB";
  if (bytes < 1000 * 1000 * 1000)
    return (bytes / (1000 * 1000)).toFixed(1) + " MB";
  return (bytes / (1000 * 1000 * 1000)).toFixed(1) + " GB";
}

function formatLastModified(dateString) {
  if (!dateString) return "-";
  try {
    return dayjs(dateString).fromNow();
  } catch (e) {
    return "-";
  }
}

function getFileName(path) {
  const parts = path.split("/");
  return parts[parts.length - 1] || path;
}

function navigateToTab(tab) {
  switch (tab) {
    case "files":
      router.push(
        `/${props.repoType}s/${props.namespace}/${props.name}/tree/${currentBranch.value}`,
      );
      break;
    case "commits":
      router.push(
        `/${props.repoType}s/${props.namespace}/${props.name}/commits/${currentBranch.value}`,
      );
      break;
    case "metadata":
      router.push({
        path: `/${props.repoType}s/${props.namespace}/${props.name}`,
        query: { tab: "metadata" },
      });
      break;
    case "viewer":
      router.push({
        path: `/${props.repoType}s/${props.namespace}/${props.name}`,
        query: { tab: "viewer" },
      });
      break;
    default:
      router.push(`/${props.repoType}s/${props.namespace}/${props.name}`);
  }
}

function navigateToSettings() {
  router.push(`/${props.repoType}s/${props.namespace}/${props.name}/settings`);
}

function navigateToUpload() {
  router.push(
    `/${props.repoType}s/${props.namespace}/${props.name}/upload/${currentBranch.value}`,
  );
}

function viewCommit(commitId) {
  router.push(
    `/${props.repoType}s/${props.namespace}/${props.name}/commit/${commitId}`,
  );
}

function handleBranchChange() {
  if (activeTab.value === "files") {
    router.push(
      `/${props.repoType}s/${props.namespace}/${props.name}/tree/${currentBranch.value}`,
    );
  }
}

async function checkIfNamespaceIsOrg() {
  try {
    // Check namespace type (works with fallback sources)
    const { data } = await axios.get(`/api/users/${props.namespace}/type`, {
      params: { fallback: true },
    });
    isNamespaceOrg.value = data.type === "org";
  } catch (err) {
    // If unknown, assume user
    isNamespaceOrg.value = false;
  }
}

async function loadRepoInfo() {
  loading.value = true;
  error.value = null;

  try {
    const { data } = await repoAPI.getInfo(
      props.repoType,
      props.namespace,
      props.name,
    );
    repoInfo.value = data;
    likesCount.value = data.likes || 0;

    // Check if namespace is an org (for correct linking)
    checkIfNamespaceIsOrg();

    // Check if current user has liked (only if authenticated)
    if (authStore.isAuthenticated) {
      try {
        const { data: likeData } = await likesAPI.checkLiked(
          props.repoType,
          props.namespace,
          props.name,
        );
        isLiked.value = likeData.liked;
      } catch (err) {
        console.error("Failed to check liked status:", err);
      }
    }
  } catch (err) {
    error.value = err.response?.data?.detail || "Failed to load repository";
    console.error("Failed to load repo info:", err);
  } finally {
    loading.value = false;
  }
}

async function toggleLike() {
  if (!authStore.isAuthenticated) {
    ElMessage.warning("Please login to like repositories");
    return;
  }

  if (likingInProgress.value) return;

  likingInProgress.value = true;

  try {
    if (isLiked.value) {
      // Unlike
      const { data } = await likesAPI.unlike(
        props.repoType,
        props.namespace,
        props.name,
      );
      isLiked.value = false;
      likesCount.value = data.likes_count;
      ElMessage.success("Repository unliked");
    } else {
      // Like
      const { data } = await likesAPI.like(
        props.repoType,
        props.namespace,
        props.name,
      );
      isLiked.value = true;
      likesCount.value = data.likes_count;
      ElMessage.success("Repository liked");
    }
  } catch (err) {
    console.error("Failed to toggle like:", err);
    const errorMsg =
      err.response?.data?.detail?.error || "Failed to update like status";
    ElMessage.error(errorMsg);
  } finally {
    likingInProgress.value = false;
  }
}

async function loadFileTree() {
  filesLoading.value = true;
  try {
    const { data } = await repoAPI.listTree(
      props.repoType,
      props.namespace,
      props.name,
      currentBranch.value,
      props.currentPath ? `/${props.currentPath}` : "",
      { recursive: false },
    );

    fileTree.value = data.sort((a, b) => {
      if (a.type === "directory" && b.type !== "directory") return -1;
      if (a.type !== "directory" && b.type === "directory") return 1;
      return a.path.localeCompare(b.path);
    });
  } catch (err) {
    console.error("Failed to load file tree:", err);
    fileTree.value = [];
  } finally {
    filesLoading.value = false;
  }
}

async function loadReadme() {
  readmeLoading.value = true;
  try {
    const readmeFile = fileTree.value.find(
      (f) => f.type === "file" && f.path.toLowerCase().endsWith("readme.md"),
    );

    if (!readmeFile) {
      readmeContent.value = "";
      readmeMetadata.value = {};
      return;
    }

    const downloadUrl = `/${props.repoType}s/${props.namespace}/${props.name}/resolve/${currentBranch.value}/${readmeFile.path}`;
    const response = await fetch(downloadUrl);

    if (response.ok) {
      const rawContent = await response.text();

      // Parse YAML frontmatter
      const { metadata, content } = parseYAMLFrontmatter(rawContent);
      readmeMetadata.value = normalizeMetadata(metadata);
      readmeContent.value = content ? content : " "; // Content without frontmatter for display, single space if remainder empty
    }
  } catch (err) {
    console.error("Failed to load README:", err);
    readmeContent.value = "";
    readmeMetadata.value = {};
  } finally {
    readmeLoading.value = false;
  }
}

async function loadCommits() {
  commitsLoading.value = true;
  try {
    const { data } = await repoAPI.listCommits(
      props.repoType,
      props.namespace,
      props.name,
      currentBranch.value,
      { limit: 20 },
    );

    commits.value = data.commits || [];
    commitsHasMore.value = data.hasMore || false;
    commitsNextCursor.value = data.nextCursor || null;
  } catch (err) {
    console.error("Failed to load commits:", err);
    commits.value = [];
  } finally {
    commitsLoading.value = false;
  }
}

async function loadMoreCommits() {
  if (!commitsHasMore.value || commitsLoading.value) return;

  commitsLoading.value = true;
  try {
    const { data } = await repoAPI.listCommits(
      props.repoType,
      props.namespace,
      props.name,
      currentBranch.value,
      { limit: 20, after: commitsNextCursor.value },
    );

    commits.value.push(...(data.commits || []));
    commitsHasMore.value = data.hasMore || false;
    commitsNextCursor.value = data.nextCursor || null;
  } catch (err) {
    console.error("Failed to load more commits:", err);
  } finally {
    commitsLoading.value = false;
  }
}

function handleFileClick(file) {
  if (file.type === "directory") {
    // Navigate to folder using tree route
    const newPath = props.currentPath
      ? `${props.currentPath}/${file.path}`
      : file.path;
    router.push(
      `/${props.repoType}s/${props.namespace}/${props.name}/tree/${currentBranch.value}/${newPath}`,
    );
  } else {
    // Navigate to file viewer using blob route
    const fullPath = props.currentPath
      ? `${props.currentPath}/${file.path}`
      : file.path;
    router.push(
      `/${props.repoType}s/${props.namespace}/${props.name}/blob/${currentBranch.value}/${fullPath}`,
    );
  }
}

function downloadRepo() {
  ElMessage.info("Download functionality coming soon");
}

function formatCommitDate(timestamp) {
  if (!timestamp) return "Unknown";
  return dayjs.unix(timestamp).fromNow();
}

function getProgressColor(percentage) {
  if (percentage >= 90) return "#f56c6c"; // Red
  if (percentage >= 75) return "#e6a23c"; // Orange
  return "#67c23a"; // Green
}

async function createReadme() {
  try {
    const readmeContent = `# ${props.name}\n\nAdd your project description here.\n`;

    console.log("Creating README with content:", readmeContent);
    console.log("Using branch:", currentBranch.value);

    // Commit the README file using the commit API
    const result = await repoAPI.commitFiles(
      props.repoType,
      props.namespace,
      props.name,
      currentBranch.value,
      {
        message: "Create README.md",
        files: [
          {
            path: "README.md",
            content: readmeContent,
          },
        ],
      },
    );

    console.log("Commit result:", result);

    ElMessage.success("README.md created successfully");

    // Reload file tree and README
    await loadFileTree();
    await loadReadme();
  } catch (err) {
    console.error("Failed to create README:", err);
    console.error("Error response:", err.response);
    console.error("Error data:", err.response?.data);
    const errorMsg =
      err.response?.data?.detail?.error || "Failed to create README.md";
    ElMessage.error(errorMsg);
  }
}

async function copyCloneUrl() {
  const success = await copyToClipboard(cloneUrl.value);
  if (success) {
    ElMessage.success("Clone URL copied to clipboard");
  } else {
    ElMessage.error("Failed to copy");
  }
}

async function copyGitCloneUrl() {
  const success = await copyToClipboard(gitCloneUrl.value);
  if (success) {
    ElMessage.success("Git clone URL copied to clipboard");
  } else {
    ElMessage.error("Failed to copy");
  }
}

async function copyRepoId() {
  const repoId = `${props.namespace}/${props.name}`;
  const success = await copyToClipboard(repoId);
  if (success) {
    ElMessage.success("Repository ID copied to clipboard");
  } else {
    ElMessage.error("Failed to copy");
  }
}

async function confirmDeleteFolder() {
  const folderName = pathSegments.value[pathSegments.value.length - 1];
  try {
    await ElMessageBox.confirm(
      `Are you sure you want to delete the folder "${folderName}" and all its contents? This action cannot be undone.`,
      "Delete Folder",
      {
        confirmButtonText: "Delete",
        cancelButtonText: "Cancel",
        type: "warning",
        confirmButtonClass: "el-button--danger",
      },
    );

    // User confirmed, proceed with deletion
    await deleteFolder();
  } catch {
    // User cancelled - do nothing
  }
}

async function deleteFolder() {
  deletingFolder.value = true;

  try {
    // Create commit with deletedFolder operation
    await repoAPI.commitFiles(
      props.repoType,
      props.namespace,
      props.name,
      currentBranch.value,
      {
        message: `Delete folder ${props.currentPath}`,
        operations: [
          {
            operation: "deletedFolder",
            path: props.currentPath,
          },
        ],
      },
    );

    const folderName = pathSegments.value[pathSegments.value.length - 1];
    ElMessage.success(`Folder "${folderName}" deleted successfully`);

    // Navigate back to parent folder or repo root
    if (pathSegments.value.length > 1) {
      const parentPath = pathSegments.value.slice(0, -1).join("/");
      router.push(
        `/${props.repoType}s/${props.namespace}/${props.name}/tree/${currentBranch.value}/${parentPath}`,
      );
    } else {
      router.push(
        `/${props.repoType}s/${props.namespace}/${props.name}/tree/${currentBranch.value}`,
      );
    }
  } catch (err) {
    console.error("Failed to delete folder:", err);
    const errorMsg =
      err.response?.data?.detail?.error || "Failed to delete folder";
    ElMessage.error(errorMsg);
  } finally {
    deletingFolder.value = false;
  }
}

// Watchers
watch(
  () => props.currentPath,
  () => {
    if (activeTab.value === "files") {
      loadFileTree();
    }
  },
);

watch(
  () => props.branch,
  (newBranch) => {
    currentBranch.value = newBranch;
    if (activeTab.value === "files") {
      loadFileTree();
    }
  },
);

watch(
  () => props.tab,
  async (newTab) => {
    if (newTab === "files" && fileTree.value.length === 0) {
      await loadFileTree();
    } else if (newTab === "card" && !readmeContent.value) {
      if (fileTree.value.length === 0) {
        await loadFileTree();
      }
      await loadReadme();
    } else if (newTab === "commits" && commits.value.length === 0) {
      await loadCommits();
    }
  },
);

watch(
  fileTree,
  () => {
    if (activeTab.value === "card" && !readmeContent.value) {
      loadReadme();
    }
  },
  { immediate: false },
);

// Lifecycle
onMounted(async () => {
  await loadRepoInfo();

  if (activeTab.value === "files") {
    await loadFileTree();
  } else if (activeTab.value === "card") {
    await loadFileTree();
    await loadReadme();
  } else if (activeTab.value === "viewer") {
    await loadFileTree();
  } else if (activeTab.value === "commits") {
    await loadCommits();
  }
});
</script>
