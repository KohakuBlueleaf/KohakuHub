function trimPathSlashes(value) {
  return value.replace(/^\/+|\/+$/g, "");
}

export function normalizeCatchAllParam(value) {
  if (Array.isArray(value)) {
    return value
      .filter((segment) => typeof segment === "string" && segment.length > 0)
      .map((segment) => trimPathSlashes(segment))
      .filter(Boolean)
      .join("/");
  }

  if (typeof value !== "string") {
    return "";
  }

  return trimPathSlashes(value);
}

export function resolveRepoTreeEntryPath(currentPath, entryPath) {
  const normalizedCurrentPath = normalizeCatchAllParam(currentPath);
  const normalizedEntryPath = normalizeCatchAllParam(entryPath);

  if (!normalizedCurrentPath) {
    return normalizedEntryPath;
  }

  if (!normalizedEntryPath) {
    return normalizedCurrentPath;
  }

  if (
    normalizedEntryPath === normalizedCurrentPath ||
    normalizedEntryPath.startsWith(`${normalizedCurrentPath}/`)
  ) {
    return normalizedEntryPath;
  }

  return `${normalizedCurrentPath}/${normalizedEntryPath}`;
}
