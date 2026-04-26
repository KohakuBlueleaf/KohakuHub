const REPO_SORT_PREFERENCE_KEY_PREFIX = "kohakuhub:repo-sort-preference";
const ALLOWED_REPO_SORT_PREFERENCES = new Set([
  "trending",
  "recent",
  "updated",
  "downloads",
  "likes",
]);

function buildRepoSortPreferenceKey(scope, repoType) {
  return `${REPO_SORT_PREFERENCE_KEY_PREFIX}:${scope}:${repoType}`;
}

export function getRepoSortPreference({
  scope,
  repoType,
  allowedValues = [],
  fallback = "recent",
}) {
  const allowedSet = new Set(allowedValues);

  if (typeof window === "undefined") return fallback;

  const value = window.sessionStorage.getItem(
    buildRepoSortPreferenceKey(scope, repoType),
  );
  if (!ALLOWED_REPO_SORT_PREFERENCES.has(value)) return fallback;
  if (allowedSet.size > 0 && !allowedSet.has(value)) return fallback;

  return value;
}

export function setRepoSortPreference({ scope, repoType, value }) {
  if (typeof window === "undefined") return;

  if (!ALLOWED_REPO_SORT_PREFERENCES.has(value)) {
    window.sessionStorage.removeItem(buildRepoSortPreferenceKey(scope, repoType));
    return;
  }

  window.sessionStorage.setItem(
    buildRepoSortPreferenceKey(scope, repoType),
    value,
  );
}

export function clearRepoSortPreference() {
  if (typeof window === "undefined") return;

  const keysToRemove = [];
  for (let i = 0; i < window.sessionStorage.length; i += 1) {
    const key = window.sessionStorage.key(i);
    if (key?.startsWith(`${REPO_SORT_PREFERENCE_KEY_PREFIX}:`)) {
      keysToRemove.push(key);
    }
  }

  keysToRemove.forEach((key) => window.sessionStorage.removeItem(key));
}
