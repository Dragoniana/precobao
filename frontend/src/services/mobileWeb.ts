import { Platform } from 'react-native';

export type MobileWebPlatform = 'android' | 'ios';

type MobileWebSignals = {
  runtimePlatform: string;
  userAgent: string;
  userAgentPlatform?: string;
  mobileHint?: boolean;
  displayModeStandalone?: boolean;
  navigatorStandalone?: boolean;
  touchPoints?: number;
};

export function detectMobileWebPlatform({
  runtimePlatform,
  userAgent,
  userAgentPlatform = '',
  mobileHint = false,
  displayModeStandalone = false,
  navigatorStandalone = false,
  touchPoints = 0,
}: MobileWebSignals): MobileWebPlatform | null {
  if (runtimePlatform !== 'web' || displayModeStandalone || navigatorStandalone) return null;
  const identity = `${userAgent} ${userAgentPlatform}`;
  if (/android/i.test(identity) && (mobileHint || /android/i.test(userAgent))) return 'android';
  if (/iphone|ipad|ipod|ios/i.test(identity)) return 'ios';
  if (/macintosh/i.test(identity) && touchPoints > 1) return 'ios';
  return null;
}

export function getMobileWebPlatform(): MobileWebPlatform | null {
  if (Platform.OS !== 'web' || typeof navigator === 'undefined') return null;
  let displayModeStandalone = false;
  try {
    displayModeStandalone = typeof window !== 'undefined'
      && window.matchMedia?.('(display-mode: standalone)').matches === true;
  } catch {
    displayModeStandalone = false;
  }
  const nav = navigator as Navigator & {
    userAgentData?: { mobile?: boolean; platform?: string };
    standalone?: boolean;
  };
  return detectMobileWebPlatform({
    runtimePlatform: Platform.OS,
    userAgent: nav.userAgent || '',
    userAgentPlatform: nav.userAgentData?.platform,
    mobileHint: nav.userAgentData?.mobile,
    displayModeStandalone,
    navigatorStandalone: nav.standalone === true,
    touchPoints: nav.maxTouchPoints,
  });
}
