export type DownloadPlatform = 'android' | 'ios';

export type AppRelease = {
  version: string;
  url: string | null;
  sizeBytes?: number | null;
  sha256?: string | null;
  publishedAt: string | null;
  available: boolean;
};

// Releases require an explicit availability flag, HTTPS URL, and complete
// platform-specific metadata. Empty or invalid URLs never produce a link.
export const APP_RELEASES: Readonly<Record<DownloadPlatform, AppRelease>> = {
  android: {
    version: '1.0.1-internal',
    url: 'https://precobao.app/downloads/preco-bao.apk',
    sizeBytes: 40_846_969,
    sha256: 'bd9ba1288e00b1ce2b141e41aa30ba5538d1dd4f88509f120750d6d98622bacb',
    publishedAt: '2026-11-24',
    available: true,
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
