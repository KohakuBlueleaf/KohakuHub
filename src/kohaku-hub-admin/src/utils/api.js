/**
 * Admin API client for KohakuHub
 * All functions require admin token to be passed explicitly (no storage)
 */

import axios from "axios";

/**
 * Create axios instance with admin token
 * @param {string} token - Admin token
 * @returns {import('axios').AxiosInstance} Axios instance
 */
function createAdminClient(token) {
  return axios.create({
    baseURL: "/admin/api",
    headers: {
      "X-Admin-Token": token,
    },
  });
}

// ===== User Management =====

/**
 * List all users
 * @param {string} token - Admin token
 * @param {Object} params - Query parameters
 * @param {string} params.search - Search by username or email
 * @param {number} params.limit - Max users to return
 * @param {number} params.offset - Offset for pagination
 * @returns {Promise<Object>} User list response
 */
export async function listUsers(
  token,
  { search, limit = 100, offset = 0, include_orgs = false } = {},
) {
  const client = createAdminClient(token);
  const response = await client.get("/users", {
    params: { search, limit, offset, include_orgs },
  });
  return response.data;
}

/**
 * Get user details
 * @param {string} token - Admin token
 * @param {string} username - Username
 * @returns {Promise<Object>} User information
 */
export async function getUserInfo(token, username) {
  const client = createAdminClient(token);
  const response = await client.get(`/users/${username}`);
  return response.data;
}

/**
 * Create new user
 * @param {string} token - Admin token
 * @param {Object} userData - User data
 * @returns {Promise<Object>} Created user
 */
export async function createUser(token, userData) {
  const client = createAdminClient(token);
  const response = await client.post("/users", userData);
  return response.data;
}

/**
 * Delete user
 * @param {string} token - Admin token
 * @param {string} username - Username to delete
 * @param {boolean} force - Force delete even if user owns repositories
 * @returns {Promise<Object>} Deletion result
 */
export async function deleteUser(token, username, force = false) {
  const client = createAdminClient(token);
  const response = await client.delete(`/users/${username}`, {
    params: { force },
  });
  return response.data;
}

/**
 * Set email verification status
 * @param {string} token - Admin token
 * @param {string} username - Username
 * @param {boolean} verified - Verification status
 * @returns {Promise<Object>} Updated user info
 */
export async function setEmailVerification(token, username, verified) {
  const client = createAdminClient(token);
  const response = await client.patch(
    `/users/${username}/email-verification`,
    null,
    {
      params: { verified },
    },
  );
  return response.data;
}

/**
 * Update user/org quota
 * @param {string} token - Admin token
 * @param {string} username - Username or org name
 * @param {number|null} privateQuotaBytes - Private quota in bytes (null = unlimited)
 * @param {number|null} publicQuotaBytes - Public quota in bytes (null = unlimited)
 * @returns {Promise<Object>} Updated quota info
 */
export async function updateUserQuota(
  token,
  username,
  privateQuotaBytes,
  publicQuotaBytes,
) {
  const client = createAdminClient(token);
  const response = await client.put(`/users/${username}/quota`, {
    private_quota_bytes: privateQuotaBytes,
    public_quota_bytes: publicQuotaBytes,
  });
  return response.data;
}

// ===== Quota Management =====

/**
 * Get quota information
 * @param {string} token - Admin token
 * @param {string} namespace - Username or org name
 * @param {boolean} isOrg - Is organization
 * @returns {Promise<Object>} Quota information
 */
export async function getQuota(token, namespace, isOrg = false) {
  const client = createAdminClient(token);
  const response = await client.get(`/quota/${namespace}`, {
    params: { is_org: isOrg },
  });
  return response.data;
}

/**
 * Set quota
 * @param {string} token - Admin token
 * @param {string} namespace - Username or org name
 * @param {Object} quotaData - Quota data
 * @param {number|null} quotaData.private_quota_bytes - Private quota
 * @param {number|null} quotaData.public_quota_bytes - Public quota
 * @param {boolean} isOrg - Is organization
 * @returns {Promise<Object>} Updated quota information
 */
export async function setQuota(token, namespace, quotaData, isOrg = false) {
  const client = createAdminClient(token);
  const response = await client.put(`/quota/${namespace}`, quotaData, {
    params: { is_org: isOrg },
  });
  return response.data;
}

/**
 * Recalculate storage usage
 * @param {string} token - Admin token
 * @param {string} namespace - Username or org name
 * @param {boolean} isOrg - Is organization
 * @returns {Promise<Object>} Updated quota information
 */
export async function recalculateQuota(token, namespace, isOrg = false) {
  const client = createAdminClient(token);
  const response = await client.post(`/quota/${namespace}/recalculate`, null, {
    params: { is_org: isOrg },
  });
  return response.data;
}

/**
 * Get quota overview with warnings
 * @param {string} token - Admin token
 * @returns {Promise<Object>} Quota overview data
 */
export async function getQuotaOverview(token) {
  const client = createAdminClient(token);
  const response = await client.get("/quota/overview");
  return response.data;
}

// ===== System Stats =====

/**
 * Get system statistics
 * @param {string} token - Admin token
 * @returns {Promise<Object>} System stats
 */
export async function getSystemStats(token) {
  const client = createAdminClient(token);
  const response = await client.get("/stats");
  return response.data;
}

/**
 * Get detailed system statistics
 * @param {string} token - Admin token
 * @returns {Promise<Object>} Detailed stats
 */
export async function getDetailedStats(token) {
  const client = createAdminClient(token);
  const response = await client.get("/stats/detailed");
  return response.data;
}

/**
 * Get time-series statistics
 * @param {string} token - Admin token
 * @param {number} days - Number of days
 * @returns {Promise<Object>} Time-series data
 */
export async function getTimeseriesStats(token, days = 30) {
  const client = createAdminClient(token);
  const response = await client.get("/stats/timeseries", { params: { days } });
  return response.data;
}

/**
 * Get top repositories
 * @param {string} token - Admin token
 * @param {number} limit - Number of top repos
 * @param {string} by - Sort by 'commits' or 'size'
 * @returns {Promise<Object>} Top repositories
 */
export async function getTopRepositories(token, limit = 10, by = "commits") {
  const client = createAdminClient(token);
  const response = await client.get("/stats/top-repos", {
    params: { limit, by },
  });
  return response.data;
}

/**
 * Verify admin token is valid
 * @param {string} token - Admin token
 * @returns {Promise<boolean>} True if token is valid
 */
export async function verifyAdminToken(token) {
  try {
    const client = createAdminClient(token);
    await client.get("/stats");
    return true;
  } catch (error) {
    if (error.response?.status === 401 || error.response?.status === 403) {
      return false;
    }
    throw error;
  }
}

// ===== Credentials (sessions / tokens / SSH keys) =====

/**
 * List active and expired user sessions across the deployment.
 * @param {string} token - Admin token
 * @param {Object} [options]
 * @param {string} [options.user] - Restrict to a specific username
 * @param {boolean} [options.activeOnly] - Drop expired sessions
 * @param {string} [options.createdAfter] - ISO timestamp lower bound
 * @param {number} [options.limit] - Page size
 * @param {number} [options.offset] - Page offset
 * @returns {Promise<Object>} Paginated session list
 */
export async function listAdminSessions(
  token,
  { user, activeOnly, createdAfter, limit = 100, offset = 0 } = {},
) {
  const client = createAdminClient(token);
  const params = { limit, offset };
  if (user !== undefined) params.user = user;
  if (activeOnly !== undefined) params.active_only = activeOnly;
  if (createdAfter !== undefined) params.created_after = createdAfter;
  const response = await client.get("/sessions", { params });
  return response.data;
}

/**
 * Revoke a single session by id.
 * @param {string} token - Admin token
 * @param {number} sessionId - Session row id
 * @returns {Promise<Object>} `{ revoked: 1 }`
 */
export async function revokeAdminSession(token, sessionId) {
  const client = createAdminClient(token);
  const response = await client.delete(`/sessions/${sessionId}`);
  return response.data;
}

/**
 * Bulk revoke sessions by user and/or before-timestamp filter.
 * At least one of the two must be provided; the backend rejects empty bodies.
 * @param {string} token - Admin token
 * @param {Object} body - `{ user?: string, before_ts?: string }`
 * @returns {Promise<Object>} `{ revoked: N }`
 */
export async function revokeAdminSessionsBulk(token, body) {
  const client = createAdminClient(token);
  const response = await client.post("/sessions/revoke-bulk", body);
  return response.data;
}

/**
 * List API tokens across the deployment.
 * @param {string} token - Admin token
 * @param {Object} [options]
 * @param {string} [options.user] - Restrict to a specific username
 * @param {number} [options.unusedForDays] - Only list tokens unused for N+ days (or never used)
 * @param {number} [options.limit] - Page size
 * @param {number} [options.offset] - Page offset
 * @returns {Promise<Object>} Paginated token list
 */
export async function listAdminTokens(
  token,
  { user, unusedForDays, limit = 100, offset = 0 } = {},
) {
  const client = createAdminClient(token);
  const params = { limit, offset };
  if (user !== undefined) params.user = user;
  if (unusedForDays !== undefined) params.unused_for_days = unusedForDays;
  const response = await client.get("/tokens", { params });
  return response.data;
}

/**
 * Revoke a single API token by id.
 * @param {string} token - Admin token
 * @param {number} tokenId - Token row id
 * @returns {Promise<Object>} `{ revoked: 1 }`
 */
export async function revokeAdminToken(token, tokenId) {
  const client = createAdminClient(token);
  const response = await client.delete(`/tokens/${tokenId}`);
  return response.data;
}

/**
 * List SSH public keys across the deployment.
 * @param {string} token - Admin token
 * @param {Object} [options]
 * @param {string} [options.user] - Restrict to a specific username
 * @param {number} [options.unusedForDays] - Only list keys unused for N+ days (or never used)
 * @param {number} [options.limit] - Page size
 * @param {number} [options.offset] - Page offset
 * @returns {Promise<Object>} Paginated SSH key list
 */
export async function listAdminSshKeys(
  token,
  { user, unusedForDays, limit = 100, offset = 0 } = {},
) {
  const client = createAdminClient(token);
  const params = { limit, offset };
  if (user !== undefined) params.user = user;
  if (unusedForDays !== undefined) params.unused_for_days = unusedForDays;
  const response = await client.get("/ssh-keys", { params });
  return response.data;
}

/**
 * Revoke (delete) a single SSH key by id.
 * @param {string} token - Admin token
 * @param {number} keyId - SSH key row id
 * @returns {Promise<Object>} `{ revoked: 1 }`
 */
export async function revokeAdminSshKey(token, keyId) {
  const client = createAdminClient(token);
  const response = await client.delete(`/ssh-keys/${keyId}`);
  return response.data;
}

// ===== Dependency Health =====

/**
 * Probe Postgres / MinIO / LakeFS / SMTP and return their status.
 *
 * @param {string} token - Admin token
 * @param {Object} [options]
 * @param {number} [options.timeoutSeconds] - Per-probe timeout in seconds
 * @returns {Promise<Object>} Aggregated probe report
 */
export async function getDependencyHealth(token, { timeoutSeconds } = {}) {
  const client = createAdminClient(token);
  const params = {};
  if (timeoutSeconds !== undefined && timeoutSeconds !== null) {
    params.timeout_seconds = timeoutSeconds;
  }
  const response = await client.get("/health/dependencies", { params });
  return response.data;
}

// ===== Repository Management =====

/**
 * List all repositories
 * @param {string} token - Admin token
 * @param {Object} params - Query parameters
 * @param {string} params.search - Search by repository full_id or name
 * @param {string} params.repo_type - Filter by type (model/dataset/space)
 * @param {string} params.namespace - Filter by namespace
 * @param {number} params.limit - Max repositories to return
 * @param {number} params.offset - Offset for pagination
 * @returns {Promise<Object>} Repository list
 */
export async function listRepositories(
  token,
  { search, repo_type, namespace, limit = 100, offset = 0 } = {},
) {
  const client = createAdminClient(token);
  const response = await client.get("/repositories", {
    params: { search, repo_type, namespace, limit, offset },
  });
  return response.data;
}

/**
 * Get repository details
 * @param {string} token - Admin token
 * @param {string} repo_type - Repository type
 * @param {string} namespace - Namespace
 * @param {string} name - Repository name
 * @returns {Promise<Object>} Repository details
 */
export async function getRepositoryDetails(token, repo_type, namespace, name) {
  const client = createAdminClient(token);
  const response = await client.get(
    `/repositories/${repo_type}/${namespace}/${name}`,
  );
  return response.data;
}

/**
 * Get repository files with LFS metadata
 * @param {string} token - Admin token
 * @param {string} repo_type - Repository type
 * @param {string} namespace - Namespace
 * @param {string} name - Repository name
 * @param {string} ref - Branch or commit reference
 * @returns {Promise<Object>} File list with LFS info
 */
export async function getRepositoryFiles(
  token,
  repo_type,
  namespace,
  name,
  ref = "main",
) {
  const client = createAdminClient(token);
  const response = await client.get(
    `/repositories/${repo_type}/${namespace}/${name}/files`,
    { params: { ref } },
  );
  return response.data;
}

/**
 * Get repository storage breakdown
 * @param {string} token - Admin token
 * @param {string} repo_type - Repository type
 * @param {string} namespace - Namespace
 * @param {string} name - Repository name
 * @returns {Promise<Object>} Storage analytics
 */
export async function getRepositoryStorageBreakdown(
  token,
  repo_type,
  namespace,
  name,
) {
  const client = createAdminClient(token);
  const response = await client.get(
    `/repositories/${repo_type}/${namespace}/${name}/storage-breakdown`,
  );
  return response.data;
}

// ===== Commit History =====

/**
 * List commits
 * @param {string} token - Admin token
 * @param {Object} params - Query parameters
 * @returns {Promise<Object>} Commit list
 */
export async function listCommits(
  token,
  { repo_full_id, username, limit = 100, offset = 0 } = {},
) {
  const client = createAdminClient(token);
  const response = await client.get("/commits", {
    params: { repo_full_id, username, limit, offset },
  });
  return response.data;
}

// ===== S3 Storage =====

/**
 * List S3 buckets
 * @param {string} token - Admin token
 * @returns {Promise<Object>} Bucket list
 */
export async function listS3Buckets(token) {
  const client = createAdminClient(token);
  const response = await client.get("/storage/buckets");
  return response.data;
}

/**
 * List S3 objects in a bucket
 * @param {string} token - Admin token
 * @param {string} bucket - Bucket name (empty = use configured bucket)
 * @param {Object} params - Query parameters
 * @returns {Promise<Object>} Object list
 */
export async function listS3Objects(
  token,
  bucket,
  { prefix = "", limit = 1000 } = {},
) {
  const client = createAdminClient(token);
  // Use /storage/objects (no bucket) to use configured bucket
  const url = bucket ? `/storage/objects/${bucket}` : "/storage/objects";
  const response = await client.get(url, {
    params: { prefix, limit },
  });
  return response.data;
}

// ===== Utility Functions =====

/**
 * Format bytes to human-readable size (decimal units: 1000 bytes = 1 KB)
 * @param {number} bytes - Bytes
 * @param {number} decimals - Decimal places
 * @returns {string} Formatted size
 */
export function formatBytes(bytes, decimals = 2) {
  if (bytes === null || bytes === undefined) return "Unlimited";
  if (bytes === 0) return "0 Bytes";

  const k = 1000;
  const dm = decimals < 0 ? 0 : decimals;
  const sizes = ["Bytes", "KB", "MB", "GB", "TB", "PB"];

  const i = Math.floor(Math.log(bytes) / Math.log(k));

  return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + " " + sizes[i];
}

/**
 * Parse human-readable size to bytes (decimal units: 1 KB = 1000 bytes)
 * @param {string} sizeStr - Size string (e.g., "10GB", "500MB")
 * @returns {number|null} Bytes, or null for unlimited
 */
export function parseSize(sizeStr) {
  if (!sizeStr || sizeStr.toLowerCase() === "unlimited") return null;

  const units = {
    b: 1,
    kb: 1000,
    mb: 1000 ** 2,
    gb: 1000 ** 3,
    tb: 1000 ** 4,
    pb: 1000 ** 5,
  };

  const match = sizeStr.match(/^(\d+(?:\.\d+)?)\s*([a-z]+)$/i);
  if (!match) return null;

  const value = parseFloat(match[1]);
  const unit = match[2].toLowerCase();

  return Math.floor(value * (units[unit] || 1));
}

/**
 * Recalculate storage for all repositories (bulk operation)
 * @param {string} token - Admin token
 * @param {Object} params - Query parameters
 * @param {string} params.repo_type - Optional filter by repository type
 * @param {string} params.namespace - Optional filter by namespace
 * @returns {Promise<Object>} Recalculation summary
 */
export async function recalculateAllRepoStorage(
  token,
  { repo_type, namespace } = {},
) {
  const client = createAdminClient(token);
  const response = await client.post("/repositories/recalculate-all", null, {
    params: { repo_type, namespace },
  });
  return response.data;
}

// ===== Invitation Management =====

/**
 * Create registration invitation
 * @param {string} token - Admin token
 * @param {Object} invitationData - Invitation data
 * @param {number|null} invitationData.org_id - Optional organization ID to join
 * @param {string} invitationData.role - Role in organization (if org_id provided)
 * @param {number|null} invitationData.max_usage - Max usage (null=one-time, -1=unlimited, N=max)
 * @param {number} invitationData.expires_days - Days until expiration
 * @returns {Promise<Object>} Created invitation
 */
export async function createRegisterInvitation(token, invitationData) {
  const client = createAdminClient(token);
  const response = await client.post("/invitations/register", invitationData);
  return response.data;
}

/**
 * List all invitations
 * @param {string} token - Admin token
 * @param {Object} params - Query parameters
 * @param {string} params.action - Filter by action type
 * @param {number} params.limit - Maximum number to return
 * @param {number} params.offset - Offset for pagination
 * @returns {Promise<Object>} Invitations list
 */
export async function listInvitations(
  token,
  { action, limit = 100, offset = 0 } = {},
) {
  const client = createAdminClient(token);
  const response = await client.get("/invitations", {
    params: { action, limit, offset },
  });
  return response.data;
}

/**
 * Delete invitation
 * @param {string} token - Admin token
 * @param {string} invitationToken - Invitation token to delete
 * @returns {Promise<Object>} Deletion result
 */
export async function deleteInvitation(token, invitationToken) {
  const client = createAdminClient(token);
  const response = await client.delete(`/invitations/${invitationToken}`);
  return response.data;
}

// ===== Global Search =====

/**
 * Global search across users, repositories, and commits
 * @param {string} token - Admin token
 * @param {string} q - Search query
 * @param {Array<string>} types - Types to search (users, repos, commits)
 * @param {number} limit - Max results per type
 * @returns {Promise<Object>} Grouped search results
 */
export async function globalSearch(
  token,
  q,
  types = ["users", "repos", "commits"],
  limit = 20,
) {
  const client = createAdminClient(token);
  const response = await client.get("/search", {
    params: { q, types, limit },
  });
  return response.data;
}

// ===== Database Viewer =====

/**
 * List database tables
 * @param {string} token - Admin token
 * @returns {Promise<Object>} Tables with schemas
 */
export async function listDatabaseTables(token) {
  const client = createAdminClient(token);
  const response = await client.get("/database/tables");
  return response.data;
}

/**
 * Get query templates
 * @param {string} token - Admin token
 * @returns {Promise<Object>} Pre-defined query templates
 */
export async function getDatabaseQueryTemplates(token) {
  const client = createAdminClient(token);
  const response = await client.get("/database/templates");
  return response.data;
}

/**
 * Execute SQL query (read-only)
 * @param {string} token - Admin token
 * @param {string} sql - SQL query string
 * @returns {Promise<Object>} Query results
 */
export async function executeDatabaseQuery(token, sql) {
  const client = createAdminClient(token);
  const response = await client.post("/database/query", { sql });
  return response.data;
}

// ===== Fallback Sources Management =====

/**
 * List all fallback sources
 * @param {string} token - Admin token
 * @param {Object} params - Query parameters
 * @param {string} params.namespace - Filter by namespace
 * @param {boolean} params.enabled - Filter by enabled status
 * @returns {Promise<Array>} Fallback sources list
 */
export async function listFallbackSources(token, { namespace, enabled } = {}) {
  const client = createAdminClient(token);
  const response = await client.get("/fallback-sources", {
    params: { namespace, enabled },
  });
  return response.data;
}

/**
 * Get specific fallback source
 * @param {string} token - Admin token
 * @param {number} sourceId - Source ID
 * @returns {Promise<Object>} Fallback source details
 */
export async function getFallbackSource(token, sourceId) {
  const client = createAdminClient(token);
  const response = await client.get(`/fallback-sources/${sourceId}`);
  return response.data;
}

/**
 * Create new fallback source
 * @param {string} token - Admin token
 * @param {Object} sourceData - Source data
 * @param {string} sourceData.namespace - Namespace ("" for global)
 * @param {string} sourceData.url - Base URL
 * @param {string} sourceData.token - Optional API token
 * @param {number} sourceData.priority - Priority (lower = higher)
 * @param {string} sourceData.name - Display name
 * @param {string} sourceData.source_type - "huggingface" or "kohakuhub"
 * @param {boolean} sourceData.enabled - Enabled status
 * @returns {Promise<Object>} Created fallback source
 */
export async function createFallbackSource(token, sourceData) {
  const client = createAdminClient(token);
  const response = await client.post("/fallback-sources", sourceData);
  return response.data;
}

/**
 * Update fallback source
 * @param {string} token - Admin token
 * @param {number} sourceId - Source ID
 * @param {Object} updateData - Fields to update
 * @returns {Promise<Object>} Updated fallback source
 */
export async function updateFallbackSource(token, sourceId, updateData) {
  const client = createAdminClient(token);
  const response = await client.put(
    `/fallback-sources/${sourceId}`,
    updateData,
  );
  return response.data;
}

/**
 * Delete fallback source
 * @param {string} token - Admin token
 * @param {number} sourceId - Source ID
 * @returns {Promise<Object>} Deletion result
 */
export async function deleteFallbackSource(token, sourceId) {
  const client = createAdminClient(token);
  const response = await client.delete(`/fallback-sources/${sourceId}`);
  return response.data;
}

/**
 * Get fallback cache statistics
 * @param {string} token - Admin token
 * @returns {Promise<Object>} Cache stats
 */
export async function getFallbackCacheStats(token) {
  const client = createAdminClient(token);
  const response = await client.get("/fallback-sources/cache/stats");
  return response.data;
}

/**
 * Clear fallback cache
 * @param {string} token - Admin token
 * @returns {Promise<Object>} Clear result
 */
export async function clearFallbackCache(token) {
  const client = createAdminClient(token);
  const response = await client.delete("/fallback-sources/cache/clear");
  return response.data;
}

/**
 * Evict every cached binding for one repo across all user buckets.
 * Bumps the repo's generation counter so any in-flight probe's
 * ``safe_set`` is rejected. Use as a surgical alternative to the
 * global ``clearFallbackCache`` when only one repo's cache is stale.
 *
 * @param {string} token - Admin token
 * @param {("model"|"dataset"|"space")} repoType
 * @param {string} namespace - Repository namespace (no slashes)
 * @param {string} name - Repository name (no slashes)
 * @returns {Promise<{success: boolean, evicted: number, repo_type: string, namespace: string, name: string}>}
 */
export async function invalidateFallbackRepoCache(
  token,
  repoType,
  namespace,
  name,
) {
  const client = createAdminClient(token);
  const response = await client.delete(
    `/fallback-sources/cache/repo/${encodeURIComponent(repoType)}/${encodeURIComponent(namespace)}/${encodeURIComponent(name)}`,
  );
  return response.data;
}

/**
 * Evict every cached binding for one user across all repos, addressed
 * by numeric ``user_id``. Bumps that user's generation counter.
 *
 * @param {string} token - Admin token
 * @param {number} userId - User PK
 * @returns {Promise<{success: boolean, evicted: number, user_id: number}>}
 */
export async function invalidateFallbackUserCacheById(token, userId) {
  const client = createAdminClient(token);
  const response = await client.delete(
    `/fallback-sources/cache/user/${encodeURIComponent(userId)}`,
  );
  return response.data;
}

/**
 * Evict every cached binding for one user across all repos, addressed
 * by ``username``. Convenience wrapper that the backend resolves to
 * ``user_id`` server-side.
 *
 * @param {string} token - Admin token
 * @param {string} username - Username (case-sensitive)
 * @returns {Promise<{success: boolean, evicted: number, user_id: number, username: string}>}
 */
export async function invalidateFallbackUserCacheByUsername(token, username) {
  const client = createAdminClient(token);
  const response = await client.delete(
    `/fallback-sources/cache/username/${encodeURIComponent(username)}`,
  );
  return response.data;
}

/**
 * Atomically replace the entire ``FallbackSource`` table with the given
 * draft. Powers the chain-tester's "Push to system" button after the
 * operator has staged a multi-edit batch.
 *
 * On success, the server clears the fallback cache (bumping
 * ``global_gen``) so any in-flight probe's safe_set is rejected.
 *
 * @param {string} token - Admin token
 * @param {Array<Object>} sources - The complete draft list. Each entry
 *   has ``namespace``, ``url``, optional ``token``, ``priority``,
 *   ``name``, ``source_type``, ``enabled``.
 * @returns {Promise<{success: boolean, replaced: number, before: number, after: number}>}
 */
export async function bulkReplaceFallbackSources(token, sources) {
  const client = createAdminClient(token);
  const response = await client.put(
    "/fallback/sources-bulk-replace",
    { sources },
  );
  return response.data;
}

/**
 * Run a unified local→fallback chain simulation against an
 * operator-supplied draft source list and identity.
 *
 * Calls the single ``/admin/api/fallback/test/simulate`` endpoint
 * (#78 redesign v2). Pure read — never writes the production cache,
 * never holds the binding lock, and (crucially) the draft source
 * list is NOT applied to the live config, so it's safe to test
 * "what if I added source X" hypotheticals.
 *
 * The returned ProbeReport puts the local hop first (decision is
 * one of LOCAL_HIT / LOCAL_FILTERED / LOCAL_MISS / LOCAL_OTHER_ERROR)
 * and only walks the fallback chain on LOCAL_MISS — same gating
 * rule the production ``with_repo_fallback`` decorator uses, so
 * "simulate says local hit" means production would hit local too.
 *
 * @param {string} token - Admin token
 * @param {Object} payload
 * @param {("info"|"tree"|"resolve"|"paths_info")} payload.op
 * @param {("model"|"dataset"|"space")} payload.repo_type
 * @param {string} payload.namespace
 * @param {string} payload.name
 * @param {string} [payload.revision]
 * @param {string} [payload.file_path]
 * @param {Array<string>} [payload.paths]
 * @param {Array<Object>} payload.sources - Draft sources
 *   (``{name, url, source_type, token?, priority?}``).
 * @param {string} [payload.as_username] - Identity to impersonate.
 *   ``as_username`` wins over ``as_user_id`` if both supplied;
 *   anonymous if both absent. Real impersonation in simulate mode —
 *   not possible in the live-real probe (admin can't bear other
 *   users' tokens).
 * @param {number} [payload.as_user_id]
 * @param {Object<string,string>} [payload.header_tokens] - Per-URL
 *   token overlay applied on top of the impersonated user's DB
 *   tokens. Mirrors production's ``Bearer xxx|url,token|...``
 *   precedence (header wins).
 * @returns {Promise<Object>} ProbeReport (``op``, ``repo_id``,
 *   ``revision``, ``file_path``, ``attempts[]``, ``final_outcome``,
 *   ``bound_source``, ``duration_ms``, ``final_response``).
 */
export async function runFallbackChainSimulate(token, payload) {
  const client = createAdminClient(token);
  const response = await client.post("/fallback/test/simulate", payload);
  return response.data;
}

/**
 * Decode an ``X-Chain-Trace`` response header (base64-encoded JSON
 * envelope ``{"version": 1, "hops": [...]}``) into the hop array.
 *
 * Tolerates malformed input by returning ``[]`` so the caller never has
 * to catch — useful because ``X-Chain-Trace`` is only set by routes
 * decorated with ``with_repo_fallback`` and an off-path response (e.g.
 * a 401 from ``get_optional_user`` before the decorator runs) won't
 * carry the header.
 *
 * @param {string|undefined|null} headerValue
 * @returns {Array<Object>} Array of hop dicts (see ``trace.py``).
 */
export function decodeChainTraceHeader(headerValue) {
  if (!headerValue) return [];
  try {
    const decoded = atob(headerValue);
    const parsed = JSON.parse(decoded);
    if (parsed && Array.isArray(parsed.hops)) return parsed.hops;
  } catch (_e) {
    // Defensive: malformed header → empty trace, render shows
    // "no chain data available" rather than blowing up the UI.
  }
  return [];
}

// Mirrored on the backend at
// ``src/kohakuhub/api/fallback/core.py:_RELEVANT_HEADERS``. When
// adding / removing entries here, update the backend list too. They're
// deliberately separate copies (build-time bundle decoupling) but
// semantically the same allowlist; small drift between the two is
// expected (frontend has ``x-source-count`` + ``x-chain-trace``
// because the SPA reads them; backend has ``content-length`` because
// it forwards it). Cross-check the backend file when in doubt.
const _PROBE_RELEVANT_HEADERS = new Set([
  "content-type",
  "etag",
  "location",
  "x-error-code",
  "x-error-message",
  "x-linked-etag",
  "x-linked-size",
  "x-repo-commit",
  "x-source",
  "x-source-url",
  "x-source-status",
  "x-source-count",
  "x-chain-trace",
  "www-authenticate",
]);

function _curatedHeadersFromFetch(headers) {
  // ``fetch`` returns a ``Headers`` instance (iterable of
  // ``[name, value]`` pairs, names lowercased). Filter to the curated
  // set so the timeline doesn't drown in date/server/keep-alive noise.
  const out = {};
  if (!headers || typeof headers.entries !== "function") return out;
  for (const [k, v] of headers.entries()) {
    if (_PROBE_RELEVANT_HEADERS.has(k.toLowerCase())) {
      out[k] = v;
    }
  }
  return out;
}

function _curatedHeaders(rawHeaders) {
  // axios normalizes header keys to lowercase in browsers (XHR
  // ``getAllResponseHeaders`` returns lowercase). We filter to the
  // curated set used elsewhere in the chain tester so the timeline
  // doesn't drown in date/server/keep-alive noise.
  const out = {};
  if (!rawHeaders) return out;
  for (const [k, v] of Object.entries(rawHeaders)) {
    if (_PROBE_RELEVANT_HEADERS.has(k.toLowerCase())) {
      out[k] = v;
    }
  }
  return out;
}

function _bodyPreview(data) {
  // Browser-side body preview, capped at 4096 chars to mirror the
  // backend ``_BODY_PREVIEW_LIMIT``. Stringify objects (axios already
  // parses JSON responses) and pass strings through verbatim.
  if (data == null) return "";
  let text;
  if (typeof data === "string") {
    text = data;
  } else {
    try {
      text = JSON.stringify(data, null, 2);
    } catch (_e) {
      text = String(data);
    }
  }
  return text.length > 4096 ? text.slice(0, 4096) : text;
}

/**
 * Build the URL + HTTP method for a given chain-tester operation.
 *
 * Mirrors the routes defined in ``src/kohakuhub/main.py`` /
 * ``api/files.py`` / ``api/repo/routers/info.py`` /
 * ``api/repo/routers/tree.py`` so a real request from this helper goes
 * through the exact handler chain a production hf_hub client would.
 *
 * @param {Object} target
 * @param {string} target.op - "info" | "tree" | "resolve" | "paths_info"
 * @param {string} target.repo_type
 * @param {string} target.namespace
 * @param {string} target.name
 * @param {string} [target.revision]
 * @param {string} [target.file_path]
 * @returns {{url: string, method: "get"|"head"|"post"}}
 */
export function buildProbeRequestTarget({
  op,
  repo_type,
  namespace,
  name,
  revision,
  file_path,
}) {
  const ns = encodeURIComponent(namespace);
  const nm = encodeURIComponent(name);
  const rev = encodeURIComponent(revision || "main");
  // file_path is the raw repo-internal path; encode segments
  // individually so slashes survive (matches HF's resolve URL shape).
  const path = (file_path || "")
    .split("/")
    .map((seg) => encodeURIComponent(seg))
    .join("/");
  switch (op) {
    case "info":
      return { url: `/api/${repo_type}s/${ns}/${nm}`, method: "get" };
    case "tree":
      return {
        url: `/api/${repo_type}s/${ns}/${nm}/tree/${rev}${path ? "/" + path : ""}`,
        method: "get",
      };
    case "resolve":
      return {
        url: `/${repo_type}s/${ns}/${nm}/resolve/${rev}/${path}`,
        method: "head",
      };
    case "paths_info":
      return {
        url: `/api/${repo_type}s/${ns}/${nm}/paths-info/${rev}`,
        method: "post",
      };
    default:
      throw new Error(`Unknown probe op: ${op}`);
  }
}

// ===========================================================================
// Per-probe trace cookie (#78 v3)
// ===========================================================================
//
// W3C Fetch spec strips response headers from the redirect chain
// (filtered ``opaqueredirect`` response, status=0, headers list empty)
// before JS can read them. There's no browser API that bypasses this
// — verified across fetch / XHR / Service Worker / iframe / Resource
// Timing / Server-Timing. So once a real probe walks through a 3xx
// (which resolve always does on fallback bind), the SPA never sees
// the X-Chain-Trace header on the post-redirect response.
//
// Workaround: chain tester sends ``X-Khub-Probe-Id: <uuid>`` on the
// request; backend (in ``with_repo_fallback``) Set-Cookie's the same
// trace under ``_khub_chain_trace_<uuid>`` alongside the X-Chain-Trace
// header. Cookies live in the cookie jar, which survives the redirect.
// SPA reads its cookie after the request settles, then deletes it so
// document.cookie doesn't accumulate stale trace blobs.

const PROBE_ID_HEADER = "X-Khub-Probe-Id";
const TRACE_COOKIE_PREFIX = "_khub_chain_trace_";

/**
 * Generate a fresh per-call probe id. Used as the cookie-name suffix
 * on the trace-pickup channel so concurrent probes don't clobber each
 * other.
 *
 * Not security-sensitive — just needs to be unique per call.
 */
function _generateProbeId() {
  if (
    typeof window !== "undefined" &&
    window.crypto &&
    typeof window.crypto.randomUUID === "function"
  ) {
    return window.crypto.randomUUID();
  }
  // Fallback for older runtimes (and jsdom in vitest, which lacks
  // ``crypto.randomUUID`` on some Node versions): timestamp + random.
  return `p-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

/**
 * Look up the per-probe trace cookie. Returns the encoded base64 trace
 * value or null if no such cookie is set (e.g. backend doesn't yet
 * support cookie injection, or the request didn't go through
 * ``with_repo_fallback``).
 *
 * Cookie value is base64-encoded JSON — base64 alphabet
 * (``[A-Za-z0-9+/=]``) is cookie-value-safe, so no URL decoding needed.
 */
function _readProbeCookie(probeId) {
  const name = `${TRACE_COOKIE_PREFIX}${probeId}`;
  const match = document.cookie.match(
    new RegExp(`(?:^|;\\s*)${name}=([^;]+)`),
  );
  if (!match) return null;
  let value = match[1];
  // Defensive: if some upstream (Python ``http.cookies.SimpleCookie``,
  // certain reverse proxies, or RFC-6265 quoted-pair handling) wraps
  // the cookie value in double quotes, strip them before handing to
  // ``atob`` — which would otherwise reject ``"`` as a non-base64
  // character. Backend ``inject_trace_cookie`` now writes the raw
  // header line unquoted, so this is a belt-and-suspenders guard.
  if (value.length >= 2 && value.startsWith('"') && value.endsWith('"')) {
    value = value.slice(1, -1);
  }
  return value;
}

/**
 * Drop the per-probe trace cookie after pickup. Without this,
 * ``document.cookie`` accumulates stale trace blobs across probes —
 * not a security issue (Max-Age=300s caps each one), but messy.
 */
function _clearProbeCookie(probeId) {
  document.cookie =
    `${TRACE_COOKIE_PREFIX}${probeId}=; ` +
    `Max-Age=0; Path=/; SameSite=Lax`;
}

/**
 * Send a real request to the local KohakuHub instance — exactly the
 * shape a production hf_hub client would issue — then read the
 * ``X-Chain-Trace`` response header to reconstruct the per-hop timeline
 * (local hop first, then any fallback hops the request walked through).
 *
 * Returns a ``ProbeReport``-shaped object so the existing tester UI
 * can render it without changes:
 *
 * - ``attempts`` — one entry per hop in the trace (kind="local" first,
 *   then "fallback"). Mirrors the backend ``ProbeAttempt`` schema for
 *   the keys the timeline UI reads.
 * - ``final_outcome`` — the decision of the bound hop, or
 *   ``CHAIN_EXHAUSTED``.
 * - ``bound_source`` — ``{name, url}`` of the hop that bound (or
 *   ``null`` if exhausted).
 * - ``final_response`` — ``{status_code, headers, body_preview}`` of
 *   the actual axios response, so the UI shows what the production
 *   caller would receive.
 *
 * @param {Object} req
 * @param {("info"|"tree"|"resolve"|"paths_info")} req.op
 * @param {("model"|"dataset"|"space")} req.repo_type
 * @param {string} req.namespace
 * @param {string} req.name
 * @param {string} [req.revision]
 * @param {string} [req.file_path]
 * @param {Array<string>} [req.paths]
 * @param {string} [req.authorization] - Full ``Authorization`` header
 *   value, e.g. ``"Bearer khub_xxx|https://huggingface.co,hf_yyy|..."``.
 *   Omitted ⇒ anonymous request. Caller is responsible for assembling
 *   the ``|url,token|...`` external-token segments.
 * @returns {Promise<Object>} ProbeReport.
 */
export async function runFallbackProbe(req) {
  const target = buildProbeRequestTarget(req);
  // Per-call probe id: lets the backend Set-Cookie the trace under a
  // unique-name so concurrent probes don't trample each other and so
  // the SPA can pick up its own trace post redirect-follow.
  const probeId = _generateProbeId();
  const headers = {
    [PROBE_ID_HEADER]: probeId,
  };
  if (req.authorization) headers["Authorization"] = req.authorization;
  // Clear any stale cookie under this id (defensive — a fresh uuid
  // shouldn't collide, but if the SPA reuses an id by accident we
  // don't want the prior trace bleeding through).
  _clearProbeCookie(probeId);
  // Probe runs through the production handler chain, which means a
  // real cache write may happen on bind. That's intentional — the
  // tester surfaces "what would my prod call do" and the user can
  // invalidate via the eviction panel afterwards if they need to.

  const t0 = performance.now();
  let response;
  let networkError = null;
  // Use ``fetch`` with ``redirect: 'manual'`` instead of axios/XHR.
  // Why:
  //   axios uses XMLHttpRequest under the hood, which transparently
  //   follows redirects. When the chain binds to a fallback source
  //   (e.g. HF), the backend's 307 ``Location: https://huggingface.co/...``
  //   triggers a *cross-origin* redirect. XHR's same-origin mode
  //   doesn't error cleanly on this — Chrome silently drops the
  //   request, the promise never resolves *or* rejects, and the SPA
  //   hangs forever. ``fetch + redirect: 'manual'`` resolves
  //   immediately on a 3xx with a filtered ``opaqueredirect`` response
  //   (status=0, headers empty per the Fetch spec) — the *cookie* set
  //   on the same-origin 307 is already in the cookie jar at that
  //   point, so we just read it. For non-3xx responses (e.g. local
  //   info LOCAL_HIT returns 200), ``redirect: 'manual'`` is
  //   transparent and the Response is the regular ``basic`` type.
  try {
    let body;
    if (target.method === "post") {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify({ paths: req.paths || [] });
    }
    response = await fetch(target.url, {
      method: target.method.toUpperCase(),
      headers,
      body,
      // Don't follow — see comment above.
      redirect: "manual",
      // Send admin's session cookie (chain tester runs admin-only,
      // and the local handler's permission gate inspects it).
      credentials: "same-origin",
    });
  } catch (e) {
    networkError = e;
  }
  const total_ms = Math.round(performance.now() - t0);

  if (networkError || !response) {
    // Genuine network failure — DNS, refused, TLS, etc. Backend
    // never got a chance to Set-Cookie, so cookie cleanup is moot.
    return {
      final_outcome: "ERROR",
      bound_source: null,
      duration_ms: total_ms,
      attempts: [
        {
          kind: "local",
          decision: "NETWORK_ERROR",
          source_name: "local",
          source_url: null,
          source_type: null,
          method: target.method.toUpperCase(),
          upstream_path: target.url,
          status_code: null,
          x_error_code: null,
          x_error_message: null,
          duration_ms: total_ms,
          error: networkError ? networkError.message : "no response",
        },
      ],
      final_response: null,
      request: { url: target.url, method: target.method.toUpperCase() },
    };
  }

  // Two-channel trace pickup. The Fetch spec's ``opaqueredirect``
  // filter zeroes ``status`` and empties the headers list on
  // redirect responses (status=0), so:
  //   - Header (X-Chain-Trace): readable on non-redirect responses
  //     (info/tree/paths_info LOCAL_HIT, resolve LOCAL_HIT HEAD).
  //   - Per-probe cookie: set by the backend on the same-origin
  //     response (alongside the 307 + Location header). Cookie is
  //     stored regardless of redirect status — it survives the
  //     opaqueredirect filter because it lives in the cookie jar
  //     rather than on the response object.
  // Cookie is cleaned up after pickup so document.cookie doesn't
  // accumulate stale blobs.
  const isOpaqueRedirect = response.type === "opaqueredirect";
  let traceValue = null;
  if (!isOpaqueRedirect) {
    traceValue = response.headers.get("x-chain-trace");
  }
  if (!traceValue) {
    traceValue = _readProbeCookie(probeId);
  }
  _clearProbeCookie(probeId);
  const hops = decodeChainTraceHeader(traceValue);

  const attempts = hops.map((h) => ({
    kind: h.kind || "fallback",
    decision: h.decision,
    source_name: h.source_name || "(unknown)",
    source_url: h.source_url || null,
    source_type: h.source_type || null,
    method: h.method || target.method.toUpperCase(),
    upstream_path: h.upstream_path || null,
    status_code: h.status_code,
    x_error_code: h.x_error_code,
    x_error_message: h.x_error_message,
    duration_ms: h.duration_ms,
    error: h.error || null,
    response_headers: null,
    response_body_preview: null,
  }));

  // Walk hops in order to find the binding decision. The first hop
  // with a binding outcome (LOCAL_HIT/LOCAL_FILTERED/LOCAL_OTHER_ERROR
  // /BIND_AND_RESPOND/BIND_AND_PROPAGATE) wins; otherwise the chain
  // exhausted.
  let final_outcome = "CHAIN_EXHAUSTED";
  let bound_source = null;
  for (const a of attempts) {
    const d = a.decision;
    if (
      d === "LOCAL_HIT" ||
      d === "LOCAL_FILTERED" ||
      d === "LOCAL_OTHER_ERROR" ||
      d === "BIND_AND_RESPOND" ||
      d === "BIND_AND_PROPAGATE"
    ) {
      final_outcome = d;
      bound_source = { name: a.source_name, url: a.source_url };
      break;
    }
  }

  // ``final_response`` shows what a production hf_hub client would
  // see for this exact request. Two paths:
  //   - opaqueredirect (status=0, headers empty per Fetch spec): the
  //     response.status / response.headers are inaccessible via fetch
  //     API, so we reconstruct from the bound hop's recorded values.
  //     This is the binding outcome the backend reported in the
  //     trace; a real hf_hub client would follow the redirect and
  //     receive HF's 200, but the chain tester surfaces what the
  //     KohakuHub layer decided + the redirect target it issued.
  //   - basic / cors (200 etc.): regular fetch response, headers and
  //     body readable. Stream the body once for preview.
  let final_response;
  if (isOpaqueRedirect) {
    const boundHop = hops.find(
      (h) => h.decision === "BIND_AND_RESPOND"
              || h.decision === "BIND_AND_PROPAGATE"
              || h.decision === "LOCAL_HIT"
              || h.decision === "LOCAL_FILTERED"
              || h.decision === "LOCAL_OTHER_ERROR",
    );
    final_response = boundHop
      ? {
          status_code: boundHop.status_code,
          headers: {},  // opaqueredirect: real headers unreadable
          body_preview:
            "[redirect — body served by upstream after redirect-follow]",
        }
      : null;
  } else {
    let bodyText = "";
    try {
      bodyText = await response.text();
    } catch (_e) {
      bodyText = "";
    }
    final_response = {
      status_code: response.status,
      headers: _curatedHeadersFromFetch(response.headers),
      body_preview: _bodyPreview(bodyText),
    };
  }

  return {
    final_outcome,
    bound_source,
    duration_ms: total_ms,
    attempts,
    final_response,
    request: { url: target.url, method: target.method.toUpperCase() },
  };
}

// ===== Repository Management =====

/**
 * Delete repository (admin)
 * @param {string} token - Admin token
 * @param {string} repoType - Repository type (model/dataset/space)
 * @param {string} namespace - Repository namespace
 * @param {string} name - Repository name
 * @returns {Promise<Object>} Deletion result
 */
export async function deleteRepositoryAdmin(token, repoType, namespace, name) {
  const response = await axios.delete("/api/repos/delete", {
    headers: { "X-Admin-Token": token },
    data: { type: repoType, name: name, organization: namespace },
  });
  return response.data;
}

/**
 * Move repository (admin)
 * @param {string} token - Admin token
 * @param {string} repoType - Repository type
 * @param {string} namespace - Source namespace
 * @param {string} name - Source name
 * @param {string} toNamespace - Target namespace
 * @param {string} toName - Target name
 * @returns {Promise<Object>} Move result
 */
export async function moveRepositoryAdmin(
  token,
  repoType,
  namespace,
  name,
  toNamespace,
  toName,
) {
  const response = await axios.post(
    "/api/repos/move",
    {
      fromRepo: `${namespace}/${name}`,
      toRepo: `${toNamespace}/${toName}`,
      type: repoType,
    },
    {
      headers: { "X-Admin-Token": token },
    },
  );
  return response.data;
}

/**
 * Squash repository (admin)
 * @param {string} token - Admin token
 * @param {string} repoType - Repository type
 * @param {string} namespace - Repository namespace
 * @param {string} name - Repository name
 * @returns {Promise<Object>} Squash result
 */
export async function squashRepositoryAdmin(token, repoType, namespace, name) {
  const response = await axios.post(
    "/api/repos/squash",
    {
      repo: `${namespace}/${name}`,
      type: repoType,
    },
    {
      headers: { "X-Admin-Token": token },
    },
  );
  return response.data;
}

// ===== L2 Cache Monitoring =====

/**
 * Fetch the L2 (Valkey) cache snapshot: per-namespace hit/miss/error
 * counters, Valkey memory usage + eviction count, and the bootstrap-flush
 * metadata (last seen run_id and timestamp).
 *
 * Backed by GET /admin/api/cache/stats. The endpoint is cheap (~one
 * INFO memory call) and safe to poll on a refresh interval.
 *
 * @param {string} token - Admin token
 * @returns {Promise<Object>} { metrics: {...}, memory: {...} }
 */
export async function getCacheStats(token) {
  const client = createAdminClient(token);
  const response = await client.get("/cache/stats");
  return response.data;
}

/**
 * Zero out the in-process cache metric counters without touching cache
 * contents. Useful when measuring the effect of a config change without
 * a full process restart.
 *
 * Returns 409 if the cache is not enabled / not initialized; the caller
 * should surface that as a friendly message.
 *
 * @param {string} token - Admin token
 * @returns {Promise<Object>} { reset: true }
 */
export async function resetCacheMetrics(token) {
  const client = createAdminClient(token);
  const response = await client.post("/cache/metrics/reset");
  return response.data;
}

// ===== S3 Storage Management =====

/**
 * Delete S3 object
 * @param {string} token - Admin token
 * @param {string} key - Object key
 * @returns {Promise<Object>} Deletion result
 */
export async function deleteS3Object(token, key) {
  const client = createAdminClient(token);
  const response = await client.delete(
    `/storage/objects/${encodeURIComponent(key)}`,
  );
  return response.data;
}

/**
 * Prepare S3 prefix deletion (step 1)
 * @param {string} token - Admin token
 * @param {string} prefix - S3 prefix
 * @returns {Promise<Object>} Confirmation token and estimated count
 */
export async function prepareDeleteS3Prefix(token, prefix) {
  const client = createAdminClient(token);
  const response = await client.post("/storage/prefix/prepare-delete", null, {
    params: { prefix },
  });
  return response.data;
}

/**
 * Delete S3 prefix (step 2)
 * @param {string} token - Admin token
 * @param {string} prefix - S3 prefix
 * @param {string} confirmToken - Confirmation token from prepare step
 * @returns {Promise<Object>} Deletion result with count
 */
export async function deleteS3Prefix(token, prefix, confirmToken) {
  const client = createAdminClient(token);
  const response = await client.delete("/storage/prefix", {
    params: { prefix, confirm_token: confirmToken },
  });
  return response.data;
}
