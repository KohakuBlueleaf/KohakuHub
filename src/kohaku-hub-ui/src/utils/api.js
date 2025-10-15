// src/kohaku-hub-ui/src/utils/api.js
import axios from "axios";

const api = axios.create({
  timeout: 30000,
  withCredentials: true,
});

// Request interceptor
api.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem("hf_token");
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => {
    return Promise.reject(error);
  },
);

// Response interceptor
api.interceptors.response.use(
  (response) => response,
  (error) => {
    // Don't auto-redirect on 401, let components handle it
    // This allows visitors to browse public content without login
    return Promise.reject(error);
  },
);

export default api;

/**
 * Auth API
 */
export const authAPI = {
  /**
   * Register a new user
   * @param {Object} data - { username, email, password, invitation_token? }
   * @returns {Promise} - Registration response
   */
  register: (data) => {
    const { invitation_token, ...userData } = data;
    // invitation_token must be passed as query parameter, not in body
    return api.post("/api/auth/register", userData, {
      params: invitation_token ? { invitation_token } : {},
    });
  },
  login: (data) => api.post("/api/auth/login", data),
  logout: () => api.post("/api/auth/logout"),
  me: () => api.get("/api/auth/me"),
  createToken: (data) => api.post("/api/auth/tokens/create", data),
  listTokens: () => api.get("/api/auth/tokens"),
  revokeToken: (id) => api.delete(`/api/auth/tokens/${id}`),
};

/**
 * Repo API
 * Backend expects:
 * - create: { type, name, organization, private, sdk? }
 * - delete: { type, name, organization?, sdk? }
 */
export const repoAPI = {
  /**
   * Create a new repository
   * @param {Object} data - { type: string, name: string, organization: string|null, private: boolean }
   * @returns {Promise} - { url: string, repo_id: string }
   */
  create: (data) => api.post("/api/repos/create", data),

  /**
   * Delete a repository
   * @param {Object} data - { type: string, name: string, organization?: string }
   * @returns {Promise} - { message: string }
   */
  delete: (data) => api.delete("/api/repos/delete", { data }),

  /**
   * Get repository info
   * @param {string} type - Repository type (model/dataset/space)
   * @param {string} namespace - Owner namespace
   * @param {string} name - Repository name
   * @returns {Promise} - Repository metadata
   */
  getInfo: (type, namespace, name) =>
    api.get(`/api/${type}s/${namespace}/${name}`),

  /**
   * List repositories
   * @param {string} type - Repository type (model/dataset/space)
   * @param {Object} params - Query parameters { author?, limit? }
   * @returns {Promise} - Array of repositories
   */
  listRepos: (type, params) => api.get(`/api/${type}s`, { params }),

  /**
   * Get user overview with all repositories
   * @param {string} username - Username
   * @param {number} limit - Max repos per type
   * @returns {Promise} - { models: [], datasets: [], spaces: [] }
   */
  getUserOverview: (username, limit = 100) =>
    api.get(`/api/users/${username}/repos`, { params: { limit } }),

  /**
   * List repository file tree
   * @param {string} type - Repository type
   * @param {string} namespace - Owner namespace
   * @param {string} name - Repository name
   * @param {string} revision - Branch name or commit hash
   * @param {string} path - Path within repository (with leading /)
   * @param {Object} params - Query parameters { recursive?, expand? }
   * @returns {Promise} - Array of files and directories
   */
  listTree: (type, namespace, name, revision, path, params) =>
    api.get(`/api/${type}s/${namespace}/${name}/tree/${revision}${path}`, {
      params,
    }),

  /**
   * Upload files to repository
   * @param {string} type - Repository type
   * @param {string} namespace - Owner namespace
   * @param {string} name - Repository name
   * @param {string} revision - Branch name
   * @param {Object} data - { files: Array<{path, file}>, message: string, description?: string }
   * @param {Function} onProgress - Progress callback
   * @returns {Promise} - Commit result
   */
  uploadFiles: async (type, namespace, name, revision, data, onProgress) => {
    const { uploadLFSFile, calculateSHA256 } = await import("./lfs.js");

    const repoId = `${namespace}/${name}`;
    const totalFiles = data.files.length;
    let processedFiles = 0;

    // Step 1: Calculate SHA256 for all files and call preupload API
    const fileMetadata = [];
    for (const fileItem of data.files) {
      const sha256 = await calculateSHA256(fileItem.file);
      fileMetadata.push({
        path: fileItem.path,
        size: fileItem.file.size,
        sha256: sha256,
      });
    }

    // Call preupload API to determine upload mode for each file
    const preuploadResponse = await api.post(
      `/api/${type}s/${namespace}/${name}/preupload/${revision}`,
      { files: fileMetadata },
    );

    const preuploadResults = preuploadResponse.data.files;

    // Create a map of path -> preupload result
    const preuploadMap = new Map();
    for (const result of preuploadResults) {
      preuploadMap.set(result.path, result);
    }

    // Step 2: Convert files to NDJSON format based on uploadMode from API
    const ndjsonLines = [];

    // Header
    ndjsonLines.push({
      key: "header",
      value: {
        summary: data.message,
        description: data.description || "",
      },
    });

    // Process each file based on uploadMode from preupload API
    for (let i = 0; i < data.files.length; i++) {
      const fileItem = data.files[i];
      const file = fileItem.file;
      const path = fileItem.path;
      const metadata = fileMetadata[i];
      const preuploadResult = preuploadMap.get(path);

      if (!preuploadResult) {
        throw new Error(`No preupload result for ${path}`);
      }

      // Skip files that should be ignored (already exist with same content)
      if (preuploadResult.shouldIgnore) {
        console.log(`Skipping unchanged file: ${path}`);
        processedFiles++;
        if (onProgress) onProgress(processedFiles / totalFiles);
        continue;
      }

      // Use uploadMode from backend to determine how to upload
      const uploadMode = preuploadResult.uploadMode;

      if (uploadMode === "lfs") {
        // LFS file: Upload to S3 through Git LFS
        console.log(`Uploading ${path} via LFS (${file.size} bytes)`);

        try {
          const lfsResult = await uploadLFSFile(
            repoId,
            file,
            metadata.sha256,
            (progress) => {
              const fileProgress = (processedFiles + progress) / totalFiles;
              if (onProgress) onProgress(fileProgress);
            },
          );

          // Add lfsFile operation to NDJSON
          ndjsonLines.push({
            key: "lfsFile",
            value: {
              path: path,
              oid: lfsResult.oid,
              size: lfsResult.size,
              algo: "sha256",
            },
          });

          processedFiles++;
        } catch (err) {
          console.error(`Failed to upload LFS file ${path}:`, err);
          throw err;
        }
      } else {
        // Regular file: Use inline base64 content
        console.log(
          `Uploading ${path} via standard commit (${file.size} bytes)`,
        );

        const reader = new FileReader();
        const content = await new Promise((resolve, reject) => {
          reader.onload = (e) => resolve(e.target.result);
          reader.onerror = reject;
          reader.readAsDataURL(file);
        });

        // Extract base64 content
        const base64Content = content.split(",")[1];

        ndjsonLines.push({
          key: "file",
          value: {
            path: path,
            content: base64Content,
            encoding: "base64",
          },
        });

        processedFiles++;
        if (onProgress) onProgress(processedFiles / totalFiles);
      }
    }

    // Step 3: Create commit with all operations
    const ndjson = ndjsonLines.map((line) => JSON.stringify(line)).join("\n");

    return api.post(
      `/api/${type}s/${namespace}/${name}/commit/${revision}`,
      ndjson,
      {
        headers: {
          "Content-Type": "application/x-ndjson",
        },
      },
    );
  },

  /**
   * Commit file changes to repository
   * @param {string} type - Repository type
   * @param {string} namespace - Owner namespace
   * @param {string} name - Repository name
   * @param {string} revision - Branch name
   * @param {Object} data - { files: Array<{path, content}>, message: string, description?: string }
   * @returns {Promise} - Commit result
   */
  commitFiles: async (type, namespace, name, revision, data) => {
    // Convert files to NDJSON format for commit API
    const ndjsonLines = [];

    // Header
    ndjsonLines.push({
      key: "header",
      value: {
        summary: data.message,
        description: data.description || "",
      },
    });

    // Files
    for (const file of data.files) {
      // Encode content as base64
      const base64Content = btoa(unescape(encodeURIComponent(file.content)));

      ndjsonLines.push({
        key: "file",
        value: {
          path: file.path,
          content: base64Content,
          encoding: "base64",
        },
      });
    }

    // Convert to NDJSON
    const ndjson = ndjsonLines.map((line) => JSON.stringify(line)).join("\n");

    return api.post(
      `/api/${type}s/${namespace}/${name}/commit/${revision}`,
      ndjson,
      {
        headers: {
          "Content-Type": "application/x-ndjson",
        },
      },
    );
  },

  /**
   * List commits for a repository branch
   * @param {string} type - Repository type
   * @param {string} namespace - Repository namespace
   * @param {string} name - Repository name
   * @param {string} branch - Branch name (default: main)
   * @param {Object} params - Query parameters { limit?, after? }
   * @returns {Promise} - { commits: Array, hasMore: boolean, nextCursor: string }
   */
  listCommits: (type, namespace, name, branch, params) =>
    api.get(`/api/${type}s/${namespace}/${name}/commits/${branch}`, { params }),
};

/**
 * Organization API
 */
export const orgAPI = {
  /**
   * Create a new organization
   * @param {Object} data - { name: string, description?: string }
   * @returns {Promise} - { success: boolean, name: string }
   */
  create: (data) => api.post("/org/create", data),

  /**
   * Get organization details
   * @param {string} name - Organization name
   * @returns {Promise} - { name, description, created_at }
   */
  get: (name) => api.get(`/org/${name}`),

  /**
   * Add member to organization
   * @param {string} name - Organization name
   * @param {Object} data - { username: string, role: string }
   * @returns {Promise} - { success: boolean, message: string }
   */
  addMember: (name, data) => api.post(`/org/${name}/members`, data),

  /**
   * Remove member from organization
   * @param {string} name - Organization name
   * @param {string} username - Username to remove
   * @returns {Promise} - { success: boolean, message: string }
   */
  removeMember: (name, username) =>
    api.delete(`/org/${name}/members/${username}`),

  /**
   * Update member role
   * @param {string} name - Organization name
   * @param {string} username - Username to update
   * @param {Object} data - { role: string }
   * @returns {Promise} - { success: boolean, message: string }
   */
  updateMemberRole: (name, username, data) =>
    api.put(`/org/${name}/members/${username}`, data),

  /**
   * Get user's organizations
   * @param {string} username - Username
   * @returns {Promise} - { organizations: Array<{ name, description, role }> }
   */
  getUserOrgs: (username) => api.get(`/org/users/${username}/orgs`),

  /**
   * Update organization settings
   * @param {string} orgName - Organization name
   * @param {Object} data - { description?: string }
   * @returns {Promise} - { success: boolean, message: string }
   */
  updateSettings: (orgName, data) =>
    api.put(`/api/organizations/${orgName}/settings`, data),

  /**
   * List organization members
   * @param {string} orgName - Organization name
   * @returns {Promise} - { members: Array<{ user: string, role: string }> }
   */
  listMembers: (orgName) => api.get(`/org/${orgName}/members`),
};

/**
 * Settings API
 */
export const settingsAPI = {
  /**
   * Get current user info with organizations (HuggingFace compatible)
   * @returns {Promise} - { type, name, fullname, email, emailVerified, orgs }
   */
  whoamiV2: () => api.get("/api/whoami-v2"),

  /**
   * Get user public profile
   * @param {string} username - Username
   * @returns {Promise} - { username, full_name, bio, website, social_media, created_at }
   */
  getUserProfile: (username) => api.get(`/api/users/${username}/profile`),

  /**
   * Update user settings
   * @param {string} username - Username
   * @param {Object} data - { email?: string, full_name?: string, bio?: string, website?: string, social_media?: object }
   * @returns {Promise} - { success: boolean, message: string }
   */
  updateUserSettings: (username, data) =>
    api.put(`/api/users/${username}/settings`, data),

  /**
   * Get organization public profile
   * @param {string} orgName - Organization name
   * @returns {Promise} - { name, description, bio, website, social_media, member_count, created_at }
   */
  getOrgProfile: (orgName) => api.get(`/api/organizations/${orgName}/profile`),

  /**
   * Upload user avatar
   * @param {string} username - Username
   * @param {File} file - Image file
   * @returns {Promise} - { success: boolean, message: string, size_bytes: number }
   */
  uploadUserAvatar: (username, file) => {
    const formData = new FormData();
    formData.append("file", file);
    return api.post(`/api/users/${username}/avatar`, formData, {
      headers: { "Content-Type": "multipart/form-data" },
    });
  },

  /**
   * Delete user avatar
   * @param {string} username - Username
   * @returns {Promise} - { success: boolean, message: string }
   */
  deleteUserAvatar: (username) => api.delete(`/api/users/${username}/avatar`),

  /**
   * Upload organization avatar
   * @param {string} orgName - Organization name
   * @param {File} file - Image file
   * @returns {Promise} - { success: boolean, message: string, size_bytes: number }
   */
  uploadOrgAvatar: (orgName, file) => {
    const formData = new FormData();
    formData.append("file", file);
    return api.post(`/api/organizations/${orgName}/avatar`, formData, {
      headers: { "Content-Type": "multipart/form-data" },
    });
  },

  /**
   * Delete organization avatar
   * @param {string} orgName - Organization name
   * @returns {Promise} - { success: boolean, message: string }
   */
  deleteOrgAvatar: (orgName) =>
    api.delete(`/api/organizations/${orgName}/avatar`),

  /**
   * Update repository settings
   * @param {string} repoType - Repository type (model/dataset/space)
   * @param {string} namespace - Repository namespace
   * @param {string} name - Repository name
   * @param {Object} data - { private?: boolean, gated?: string }
   * @returns {Promise} - { success: boolean, message: string }
   */
  updateRepoSettings: (repoType, namespace, name, data) =>
    api.put(`/api/${repoType}s/${namespace}/${name}/settings`, data),

  /**
   * Move/rename repository
   * @param {Object} data - { fromRepo: string, toRepo: string, type: string }
   * @returns {Promise} - { success: boolean, url: string, message: string }
   */
  moveRepo: (data) => api.post("/api/repos/move", data),

  /**
   * Squash repository (clear all history)
   * @param {Object} data - { repo: string, type: string }
   * @returns {Promise} - { success: boolean, message: string }
   */
  squashRepo: (data) => api.post("/api/repos/squash", data),

  /**
   * Create branch
   * @param {string} repoType - Repository type
   * @param {string} namespace - Repository namespace
   * @param {string} name - Repository name
   * @param {Object} data - { branch: string, revision?: string }
   * @returns {Promise} - { success: boolean, message: string }
   */
  createBranch: (repoType, namespace, name, data) =>
    api.post(`/api/${repoType}s/${namespace}/${name}/branch`, data),

  /**
   * Delete branch
   * @param {string} repoType - Repository type
   * @param {string} namespace - Repository namespace
   * @param {string} name - Repository name
   * @param {string} branch - Branch name
   * @returns {Promise} - { success: boolean, message: string }
   */
  deleteBranch: (repoType, namespace, name, branch) =>
    api.delete(`/api/${repoType}s/${namespace}/${name}/branch/${branch}`),

  /**
   * Create tag
   * @param {string} repoType - Repository type
   * @param {string} namespace - Repository namespace
   * @param {string} name - Repository name
   * @param {Object} data - { tag: string, revision?: string, message?: string }
   * @returns {Promise} - { success: boolean, message: string }
   */
  createTag: (repoType, namespace, name, data) =>
    api.post(`/api/${repoType}s/${namespace}/${name}/tag`, data),

  /**
   * Delete tag
   * @param {string} repoType - Repository type
   * @param {string} namespace - Repository namespace
   * @param {string} name - Repository name
   * @param {string} tag - Tag name
   * @returns {Promise} - { success: boolean, message: string }
   */
  deleteTag: (repoType, namespace, name, tag) =>
    api.delete(`/api/${repoType}s/${namespace}/${name}/tag/${tag}`),
};

/**
 * Validation API
 */
export const validationAPI = {
  /**
   * Check if name is available (no conflicts)
   * @param {Object} data - { name: string, namespace?: string, type?: string }
   * @returns {Promise} - { available: boolean, normalized_name: string, conflict_with?: string, message: string }
   */
  checkName: (data) => api.post("/api/validate/check-name", data),
};

/**
 * Quota API
 */
/**
 * Invitation API
 */
export const invitationAPI = {
  /**
   * Create organization invitation
   * @param {string} orgName - Organization name
   * @param {Object} data - { email: string, role: string }
   * @returns {Promise} - { success: boolean, token: string, invitation_link: string, expires_at: string }
   */
  create: (orgName, data) =>
    api.post(`/api/invitations/org/${orgName}/create`, data),

  /**
   * Get invitation details
   * @param {string} token - Invitation token
   * @returns {Promise} - { action, org_name, role, inviter_username, expires_at, is_expired, is_used }
   */
  get: (token) => api.get(`/api/invitations/${token}`),

  /**
   * Accept invitation
   * @param {string} token - Invitation token
   * @returns {Promise} - { success: boolean, message: string, org_name: string, role: string }
   */
  accept: (token) => api.post(`/api/invitations/${token}/accept`),

  /**
   * List organization invitations (admin only)
   * @param {string} orgName - Organization name
   * @returns {Promise} - { invitations: Array<{id, token, email, role, created_by, created_at, expires_at, used_at, is_pending}> }
   */
  list: (orgName) => api.get(`/api/invitations/org/${orgName}/list`),

  /**
   * Delete/cancel invitation (admin only)
   * @param {string} token - Invitation token
   * @returns {Promise} - { success: boolean, message: string }
   */
  delete: (token) => api.delete(`/api/invitations/${token}`),
};

/**
 * Quota API
 */
export const quotaAPI = {
  /**
   * Get repository quota information
   * @param {string} repoType - Repository type (model/dataset/space)
   * @param {string} namespace - Repository namespace
   * @param {string} name - Repository name
   * @returns {Promise} - Repository quota info
   */
  getRepoQuota: (repoType, namespace, name) =>
    api.get(`/api/quota/repo/${repoType}/${namespace}/${name}`),

  /**
   * Set repository quota
   * @param {string} repoType - Repository type (model/dataset/space)
   * @param {string} namespace - Repository namespace
   * @param {string} name - Repository name
   * @param {Object} data - { quota_bytes: number | null }
   * @returns {Promise} - Updated quota info
   */
  setRepoQuota: (repoType, namespace, name, data) =>
    api.put(`/api/quota/repo/${repoType}/${namespace}/${name}`, data),

  /**
   * Recalculate repository storage usage
   * @param {string} repoType - Repository type (model/dataset/space)
   * @param {string} namespace - Repository namespace
   * @param {string} name - Repository name
   * @returns {Promise} - Updated quota info
   */
  recalculateRepoStorage: (repoType, namespace, name) =>
    api.post(`/api/quota/repo/${repoType}/${namespace}/${name}/recalculate`),

  /**
   * Get detailed storage breakdown for all repos in a namespace
   * @param {string} namespace - Username or organization name
   * @returns {Promise} - List of repositories with storage info
   */
  getNamespaceRepoStorage: (namespace) =>
    api.get(`/api/quota/${namespace}/repos`),
};
