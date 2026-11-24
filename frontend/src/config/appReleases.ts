export type DownloadPlatform = 'android' | 'ios';

export type AppRelease = {
  version: string;
  url: string | null;
  sizeBytes?: number | null;
  sha256?: string | null;
  publishedAt: string | null;
  available: boolean;
};

// A download becomes available only after its signed artifact is published and
// its metadata has been reviewed. Empty URLs never produce a download link.
export const APP_RELEASES: Readonly<Record<DownloadPlatform, AppRelease>> = {
  android: {
    version: '1.0.1',
    url: null,
    sizeBytes: null,
    sha256: null,
    publishedAt: null,
    available: false,
  },
  ios: {
    version: '1.0.1',
    url: null,
    publishedAt: null,
    available: false,
  },
};

export function getAvailableAppRelease(platform: DownloadPlatform) {
  const release = APP_RELEASES[platform];
  if (!release.available || !release.url) return null;
  try {
    const url = new URL(release.url);
    if (url.protocol !== 'https:' || url.username || url.password) return null;
  } catch {
    return null;
  }
  if (platform === 'android') {
    if (!release.sizeBytes || release.sizeBytes < 1) return null;
    if (!release.sha256 || !/^[a-f0-9]{64}$/i.test(release.sha256)) return null;
  }
  return release;
}

export function formatReleaseSize(sizeBytes: number) {
  if (sizeBytes >= 1_000_000_000) return `${(sizeBytes / 1_000_000_000).toFixed(1)} GB`;
  if (sizeBytes >= 1_000_000) return `${(sizeBytes / 1_000_000).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(sizeBytes / 1_000))} KB`;
}
