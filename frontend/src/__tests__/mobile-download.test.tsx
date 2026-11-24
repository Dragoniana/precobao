import React from 'react';
import { fireEvent, render, waitFor } from '@testing-library/react-native';
import { Linking } from 'react-native';

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

it('exposes the validated Android build and keeps iOS unavailable without distribution', () => {
  expect(getAvailableAppRelease('android')).toMatchObject({
    version: '1.0.1-internal',
    url: 'https://precobao.app/downloads/preco-bao.apk',
    sizeBytes: 40_846_969,
    sha256: 'bd9ba1288e00b1ce2b141e41aa30ba5538d1dd4f88509f120750d6d98622bacb',
    publishedAt: '2026-11-24',
    available: true,
  });
  expect(getAvailableAppRelease('ios')).toBeNull();
  expect(APP_RELEASES.android.available).toBe(true);
  expect(APP_RELEASES.ios.available).toBe(false);
});

it('opens the Android download from the mobile banner and persists dismissal', async () => {
  const openURL = jest.spyOn(Linking, 'openURL').mockResolvedValue(undefined);
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
  expect(getByText('Versão 1.0.1-internal · 40.8 MB')).toBeTruthy();
  await fireEvent.press(getByRole('button', { name: 'Baixar aplicativo' }));
  await waitFor(() => expect(openURL).toHaveBeenCalledWith('https://precobao.app/downloads/preco-bao.apk'));
  await fireEvent.press(getByRole('button', { name: 'Fechar aviso de download' }));
  await waitFor(() => expect(queryByText('Baixe o app Preço Bão')).toBeNull());
  expect([...values.values()]).toContain('true');
  if (previousWindow) Object.defineProperty(globalThis, 'window', previousWindow);
  else Reflect.deleteProperty(globalThis, 'window');
  openURL.mockRestore();
});

it('keeps the download option available in mobile settings when the banner is dismissed', async () => {
  const { getByText, getByRole } = await render(<MobileAppDownloadSettings />);
  expect(getByText('Nosso aplicativo')).toBeTruthy();
  expect(getByText('Faça o download do nosso app, é grátis!')).toBeTruthy();
  expect(getByRole('button', { name: 'Baixar Preço Bão' })).toBeTruthy();
  expect(getByText('Versão 1.0.1-internal · 40.8 MB')).toBeTruthy();
});
