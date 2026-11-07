import type { Platform } from 'react-native';

type PlatformName = Platform['OS'];

const ANDROID_EMULATOR_HOST = '10.0.2.2';

function isLoopback(hostname: string) {
  const host = hostname.toLowerCase().replace(/^\[|\]$/g, '');
  return host === 'localhost' || host === '::1' || host.startsWith('127.');
}

function getLanHost(hostUri?: string) {
  if (!hostUri) return undefined;
  try {
    const hostname = new URL(`http://${hostUri}`).hostname.replace(/^\[|\]$/g, '');
    const octets = hostname.split('.').map(Number);
    const isPrivateIpv4 = octets.length === 4 && octets.every((part) => Number.isInteger(part) && part >= 0 && part <= 255) && (
      octets[0] === 10 ||
      (octets[0] === 172 && octets[1] >= 16 && octets[1] <= 31) ||
      (octets[0] === 192 && octets[1] === 168)
    );
    return isPrivateIpv4 ? hostname : undefined;
  } catch {
    return undefined;
  }
}

/**
 * Resolves API configuration without ever shipping a localhost fallback in a
 * production native app. Android's emulator uses 10.0.2.2 to reach the host.
 * Physical devices must receive the computer's LAN URL through the env var.
 */
export function resolveApiUrl(
  configuredUrl: string | undefined,
  platform: PlatformName,
  isDevelopment: boolean,
  packagerHostUri?: string,
) {
  let candidate = configuredUrl?.trim();
  const lanHost = getLanHost(packagerHostUri);

  if (!candidate) {
    if (!isDevelopment) return '';
    const developmentHost = lanHost || (platform === 'android' ? ANDROID_EMULATOR_HOST : '127.0.0.1');
    candidate = `http://${developmentHost}:8000`;
  }

  try {
    const url = new URL(candidate);
    if (url.protocol !== 'https:' && url.protocol !== 'http:') return '';
    if (!isDevelopment && (url.protocol !== 'https:' || isLoopback(url.hostname))) return '';

    if (isDevelopment && isLoopback(url.hostname)) {
      if (lanHost) {
        url.hostname = lanHost;
      } else if (platform === 'android') {
        url.hostname = ANDROID_EMULATOR_HOST;
      }
    }

    return url.toString().replace(/\/$/, '');
  } catch {
    return '';
  }
}
