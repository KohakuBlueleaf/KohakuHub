// src/kohaku-hub-ui/src/utils/repo-list-pagination.js
//
// User-level preference for the repo file-list page size, persisted in
// localStorage so a user who picked "100 per page" once does not have
// to repick it on every repo. Mirrors the shape of the indexed-tar
// listing prefs (utils/tar-listing-prefs.js) so the two read the same
// way at call sites.
//
// Page size is exposed as a small fixed list — the LakeFS-backed tree
// endpoint clamps `limit` to TREE_PAGE_SIZE (1000) anyway, and "200 max"
// keeps a single page render under jsdom's render budget in tests while
// still cutting wire volume by ~5x for a 1000-entry directory.

const PAGE_SIZE_KEY = "kohaku-repo-file-list-page-size";

export const VALID_PAGE_SIZES = [50, 100, 200];
export const DEFAULT_PAGE_SIZE = 50;

function safeStorageGet(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeStorageSet(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Private mode / quota — fall back to the in-memory ref the caller
    // is already using. Persistence is the only thing lost.
  }
}

export function readPageSize() {
  const n = Number(safeStorageGet(PAGE_SIZE_KEY));
  return VALID_PAGE_SIZES.includes(n) ? n : DEFAULT_PAGE_SIZE;
}

export function writePageSize(value) {
  const n = Number(value);
  if (!VALID_PAGE_SIZES.includes(n)) return;
  safeStorageSet(PAGE_SIZE_KEY, String(n));
}
