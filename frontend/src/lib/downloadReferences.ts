import type { DownloadItem, ReportConfig } from "@/types/config";

function exactName(value: string | undefined) {
  return value || "";
}

function countNames(names: string[]) {
  return names.reduce<Map<string, number>>((counts, name) => {
    if (name) counts.set(name, (counts.get(name) || 0) + 1);
    return counts;
  }, new Map());
}

function unambiguousJsonRenames(previous: ReportConfig["downloads"], next: ReportConfig["downloads"]) {
  // JSON edits can insert, remove, or reorder array items. Indexes only
  // identify an in-place rename when the array length is unchanged and both
  // labels are unique across their respective snapshots.
  if (previous.length !== next.length) return new Map<string, string>();

  const previousNames = previous.map((item) => exactName(item.name));
  const nextNames = next.map((item) => exactName(item.name));
  const previousCounts = countNames(previousNames);
  const nextCounts = countNames(nextNames);
  const renames = new Map<string, string>();

  previousNames.forEach((previousName, index) => {
    const nextName = nextNames[index];
    if (!previousName || !nextName || previousName === nextName) return;
    if (
      previousCounts.get(previousName) === 1
      && nextCounts.get(previousName) === undefined
      && previousCounts.get(nextName) === undefined
      && nextCounts.get(nextName) === 1
    ) {
      renames.set(previousName, nextName);
    }
  });

  return renames;
}

/**
 * Keep compare source references attached to a download when its label is
 * edited in the form.  Compare sources intentionally store the label for
 * compatibility with the runtime manifest, so a rename must update both
 * sections in the same state transition.
 */
export function updateDownloadWithReferences(
  config: ReportConfig,
  index: number,
  next: DownloadItem,
): ReportConfig {
  const previous = config.downloads[index];
  const previousName = exactName(previous?.name);
  const nextName = exactName(next.name);
  const nextDownloads = config.downloads.map((item, itemIndex) => (
    itemIndex === index ? next : item
  ));

  if (!previous || previousName === nextName) {
    return { ...config, downloads: nextDownloads };
  }

  return {
    ...config,
    downloads: nextDownloads,
    compare_sources: config.compare_sources.map((source) => (
      exactName(source.download_name) === previousName
        ? { ...source, download_name: nextName }
        : source
    )),
  };
}

/**
 * Apply the same rename rule when a complete config arrives from the JSON
 * editor. The source editor bypasses the per-card form callback, so compare
 * references must be reconciled against the previous editor snapshot here.
 */
export function syncDownloadReferences(previous: ReportConfig, next: ReportConfig): ReportConfig {
  const previousDownloads = Array.isArray(previous.downloads) ? previous.downloads : [];
  const nextDownloads = Array.isArray(next.downloads) ? next.downloads : [];
  const renames = unambiguousJsonRenames(previousDownloads, nextDownloads);
  const compareSources = (Array.isArray(next.compare_sources) ? next.compare_sources : []).map((source) => {
    const renamedTo = renames.get(exactName(source.download_name));
    return renamedTo ? { ...source, download_name: renamedTo } : source;
  });
  return {
    ...next,
    downloads: nextDownloads,
    compare_sources: compareSources,
  };
}
