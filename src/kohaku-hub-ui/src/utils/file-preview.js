// src/kohaku-hub-ui/src/utils/file-preview.js
//
// Helpers shared by RepoViewer.vue's file-list icon and FilePreviewDialog.
// Extracted so the preview-eligibility predicate and the /resolve/ URL
// builder can be unit-tested in isolation from the Vue component tree.

const PREVIEW_EXTENSIONS = new Map([
  [".safetensors", "safetensors"],
  [".parquet", "parquet"],
]);

/**
 * Return the preview kind for a given file path, or null if the file is
 * not a kind we know how to preview. Uses a case-insensitive suffix match
 * so `MODEL.SAFETENSORS` and `shard.SAFETENSORS` both count.
 */
export function getPreviewKind(path) {
  if (typeof path !== "string" || path.length === 0) return null;
  const lower = path.toLowerCase();
  for (const [ext, kind] of PREVIEW_EXTENSIONS) {
    if (lower.endsWith(ext)) return kind;
  }
  return null;
}

/**
 * Gate a repo-tree file entry for the preview icon. Directories never
 * preview — only files whose path ends in a supported extension.
 */
export function canPreviewFile(file) {
  if (!file || typeof file !== "object") return false;
  if (file.type === "directory") return false;
  return getPreviewKind(file.path) !== null;
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
