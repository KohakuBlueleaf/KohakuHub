// src/kohaku-hub-ui/src/utils/tar-listing-prefs.js
//
// User-level preferences for the indexed-tar listing surface,
// persisted in localStorage so a user's "I prefer the grid layout
// at 20 per page" choice survives modal re-opens, blob-page
// navigations, and tab restarts.
//
// Kept separate from `tar-thumbnail.js` because the toggle there
// gates *content* (whether to fetch image bytes for previews),
// while these are *layout* concerns. Mixing them would tempt
// callers to bind the wrong key.

const VIEW_MODE_KEY = "kohaku-tar-view-mode";
const PAGE_SIZE_KEY = "kohaku-tar-page-size";

export const VALID_VIEW_MODES = ["list", "grid"];
export const VALID_PAGE_SIZES = [20, 50, 100];

export const DEFAULT_VIEW_MODE = "grid";
export const DEFAULT_PAGE_SIZE = 20;

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
    // private mode / quota exceeded — silently fall back to in-memory
    // state for the current session. The runtime behaviour stays
    // correct; only persistence is lost.
  }
}

export function readViewMode() {
  const raw = safeStorageGet(VIEW_MODE_KEY);
  return VALID_VIEW_MODES.includes(raw) ? raw : DEFAULT_VIEW_MODE;
}

export function writeViewMode(value) {
  if (!VALID_VIEW_MODES.includes(value)) return;
  safeStorageSet(VIEW_MODE_KEY, value);
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
