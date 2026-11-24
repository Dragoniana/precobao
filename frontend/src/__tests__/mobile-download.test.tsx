import React from 'react';
import { fireEvent, render, waitFor } from '@testing-library/react-native';

import { APP_RELEASES, getAvailableAppRelease } from '../config/appReleases';
import { MobileAppDownloadBanner, MobileAppDownloadSettings } from '../components/MobileAppDownload';
import { detectMobileWebPlatform } from '../services/mobileWeb';

jest.mock('../services/mobileWeb', () => ({
  getMobileWebPlatform: jest.fn(() => 'android'),
  detectMobileWebPlatform: jest.requireActual('../services/mobileWeb').detectMobileWebPlatform,
}));

it('identifies mobile browsers by platform and runtime, not viewport width', () => {
  expect(detectMobileWebPlatform({ runtimePlatform: 'web', userAgent: 'Mozilla/5.0 (Linux; Android 16)' })).toBe('android');
  expect(detectMobileWebPlatform({ runtimePlatform: 'web', userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)' })).toBe('ios');
  expect(detectMobileWebPlatform({ runtimePlatform: 'web', userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' })).toBeNull();
  expect(detectMobileWebPlatform({ runtimePlatform: 'android', userAgent: 'Mozilla/5.0 (Linux; Android 16)' })).toBeNull();
  expect(detectMobileWebPlatform({ runtimePlatform: 'web', userAgent: 'Mozilla/5.0 (Linux; Android 16)', displayModeStandalone: true })).toBeNull();
});

it('does not expose a download until a complete release has a valid URL and metadata', () => {
  expect(getAvailableAppRelease('android')).toBeNull();
  expect(getAvailableAppRelease('ios')).toBeNull();
  expect(APP_RELEASES.android.url).toBeNull();
  expect(APP_RELEASES.ios.available).toBe(false);
});

it('shows the mobile banner, reports unavailable Android without opening a link, and persists dismissal', async () => {
  const values = new Map<string, string>();
  const previousWindow = Object.getOwnPropertyDescriptor(globalThis, 'window');
  Object.defineProperty(globalThis, 'window', {
    configurable: true,
    value: { localStorage: {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
    } },
  });
  const { getByRole, getByText, queryByText } = await render(<MobileAppDownloadBanner />);
  await waitFor(() => expect(getByText('Baixe o app Preço Bão')).toBeTruthy());
  await fireEvent.press(getByRole('button', { name: 'Baixar aplicativo' }));
  expect(getByText('O APK oficial para Android está sendo preparado.')).toBeTruthy();
  await fireEvent.press(getByRole('button', { name: 'Fechar aviso de download' }));
  await waitFor(() => expect(queryByText('Baixe o app Preço Bão')).toBeNull());
  expect([...values.values()]).toContain('true');
  if (previousWindow) Object.defineProperty(globalThis, 'window', previousWindow);
  else Reflect.deleteProperty(globalThis, 'window');
});

it('keeps the download option available in mobile settings when the banner is dismissed', async () => {
  const { getByText, getByRole } = await render(<MobileAppDownloadSettings />);
  expect(getByText('Nosso aplicativo')).toBeTruthy();
  expect(getByText('Faça o download do nosso app, é grátis!')).toBeTruthy();
  expect(getByRole('button', { name: 'Baixar Preço Bão' })).toBeTruthy();
  expect(getByText('O APK oficial para Android está sendo preparado.')).toBeTruthy();
});
