// src/kohaku-hub-ui/src/utils/file-preview.js
//
// Helpers shared by RepoViewer.vue's file-list icon and FilePreviewDialog.
// Extracted so the preview-eligibility predicate and the /resolve/ URL
// builder can be unit-tested in isolation from the Vue component tree.

import { hasIndexSibling } from "@/utils/indexed-tar";

const SUFFIX_PREVIEW_KINDS = new Map([
  [".safetensors", "safetensors"],
  [".parquet", "parquet"],
]);

/**
 * Return the preview kind for a given file path, or null if the file is
 * not a kind we know how to preview. Uses a case-insensitive suffix match
 * so `MODEL.SAFETENSORS` and `shard.SAFETENSORS` both count.
 *
 * `.tar` files only resolve to "indexed-tar" when a `.json` sibling
 * exists in the same listing (passed via `siblings`) or has been
 * confirmed by an out-of-band probe and recorded in
 * `confirmedTarPaths`. A bare `.tar` is not previewable — the icon
 * would otherwise light up on every plain archive in the repo.
 *
 * `confirmedTarPaths` is intended for the paginated file-list path:
 * when the `.json` sibling fell off the current page, the caller
 * (RepoViewer) probes the backend with HEAD and stores the resolved
 * tar path here so the icon still lights up.
 */
export function getPreviewKind(path, siblings = null, confirmedTarPaths = null) {
  if (typeof path !== "string" || path.length === 0) return null;
  const lower = path.toLowerCase();
  for (const [ext, kind] of SUFFIX_PREVIEW_KINDS) {
    if (lower.endsWith(ext)) return kind;
  }
  if (lower.endsWith(".tar")) {
    if (siblings && hasIndexSibling(path, siblings)) return "indexed-tar";
    if (confirmedTarPaths && hasInSet(confirmedTarPaths, path)) {
      return "indexed-tar";
    }
  }
  return null;
}

/**
 * Gate a repo-tree file entry for the preview icon. Directories never
 * preview — only files whose path ends in a supported extension.
 *
 * `siblings` (optional) is the same listing the row lives in; it
 * unlocks the "indexed-tar" kind when a sibling `.json` is present.
 * `confirmedTarPaths` (optional) is the probe-resolved set the caller
 * maintains for paginated views.
 */
export function canPreviewFile(file, siblings = null, confirmedTarPaths = null) {
  if (!file || typeof file !== "object") return false;
  if (file.type === "directory") return false;
  return getPreviewKind(file.path, siblings, confirmedTarPaths) !== null;
}

function hasInSet(maybeSet, key) {
  if (!maybeSet) return false;
  if (typeof maybeSet.has === "function") return maybeSet.has(key);
  return false;
}

/**
 * Build a same-origin /resolve/ URL for a given (repoType, namespace,
 * name, branch, path). Encodes every path segment individually so a
 * branch name with a slash (`refs/convert/parquet`) or a file path with
 * spaces survives intact.
 */
export function buildResolveUrl({ baseUrl, repoType, namespace, name, branch, path }) {
  if (!baseUrl || !repoType || !namespace || !name || !branch || !path) {
    throw new Error(
      "buildResolveUrl requires baseUrl, repoType, namespace, name, branch, path",
    );
  }
  const encodedPath = path
    .split("/")
    .map((seg) => encodeURIComponent(seg))
    .join("/");
  return `${baseUrl}/${repoType}s/${namespace}/${name}/resolve/${encodeURIComponent(
    branch,
  )}/${encodedPath}`;
}
