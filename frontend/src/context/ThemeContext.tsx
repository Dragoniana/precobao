import * as SecureStore from 'expo-secure-store';
import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { Platform, StyleSheet, useColorScheme } from 'react-native';

import { AppPalette, darkPalette, getShadow, lightPalette, ResolvedTheme, ThemePreference } from '../theme';

export const THEME_STORAGE_KEY = 'preco-bao-appearance';
const LEGACY_WEB_THEME_STORAGE_KEY = 'preco-bao:appearance';

type ThemeContextValue = {
  preference: ThemePreference;
  resolvedTheme: ResolvedTheme;
  isDark: boolean;
  isReady: boolean;
  palette: AppPalette;
  shadow: ReturnType<typeof getShadow>;
  setPreference: (preference: ThemePreference) => void;
};

const defaultTheme: ThemeContextValue = {
  preference: 'light',
  resolvedTheme: 'light',
  isDark: false,
  isReady: true,
  palette: lightPalette,
  shadow: getShadow(lightPalette),
  setPreference: () => undefined,
};

const ThemeContext = createContext<ThemeContextValue>(defaultTheme);

export function parseThemePreference(value: string | null | undefined): ThemePreference {
  return value === 'light' || value === 'dark' || value === 'system' ? value : 'system';
}

export function resolveThemePreference(
  preference: ThemePreference,
  systemScheme: 'light' | 'dark' | 'unspecified' | null | undefined,
): ResolvedTheme {
  return preference === 'system' && systemScheme === 'dark' ? 'dark'
    : preference === 'dark' ? 'dark'
      : 'light';
}

function initialWebPreference(): ThemePreference {
  if (Platform.OS !== 'web' || typeof window === 'undefined') return 'system';
  try {
    const savedPreference = window.localStorage.getItem(THEME_STORAGE_KEY);
    if (savedPreference) return parseThemePreference(savedPreference);

    const legacyPreference = window.localStorage.getItem(LEGACY_WEB_THEME_STORAGE_KEY);
    if (legacyPreference) {
      window.localStorage.setItem(THEME_STORAGE_KEY, legacyPreference);
      window.localStorage.removeItem(LEGACY_WEB_THEME_STORAGE_KEY);
    }
    return parseThemePreference(legacyPreference);
  } catch {
    return 'system';
  }
}

function initialWebScheme(): ResolvedTheme {
  if (Platform.OS !== 'web' || typeof window === 'undefined' || typeof window.matchMedia !== 'function') return 'light';
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const nativeSystemScheme = useColorScheme();
  const [webSystemScheme, setWebSystemScheme] = useState<ResolvedTheme>(initialWebScheme);
  const [preference, setPreferenceState] = useState<ThemePreference>(initialWebPreference);
  const [isReady, setIsReady] = useState(Platform.OS === 'web');

  useEffect(() => {
    if (Platform.OS === 'web') return;
    let active = true;
    SecureStore.getItemAsync(THEME_STORAGE_KEY)
      .then((stored) => { if (active) setPreferenceState(parseThemePreference(stored)); })
      .catch((error) => console.error('Failed to load saved appearance preference', error))
      .finally(() => { if (active) setIsReady(true); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (Platform.OS !== 'web' || typeof window === 'undefined' || typeof window.matchMedia !== 'function') return;
    const query = window.matchMedia('(prefers-color-scheme: dark)');
    const update = (event?: MediaQueryListEvent) => {
      setWebSystemScheme((event?.matches ?? query.matches) ? 'dark' : 'light');
    };
    update();
    if (query.addEventListener) query.addEventListener('change', update);
    else query.addListener(update);
    return () => {
      if (query.removeEventListener) query.removeEventListener('change', update);
      else query.removeListener(update);
    };
  }, []);

  const setPreference = useCallback((next: ThemePreference) => {
    setPreferenceState(next);
    if (Platform.OS === 'web' && typeof window !== 'undefined') {
      try {
        window.localStorage.setItem(THEME_STORAGE_KEY, next);
        window.localStorage.removeItem(LEGACY_WEB_THEME_STORAGE_KEY);
      } catch {
        // The in-memory choice still applies when browser storage is unavailable.
      }
    } else {
      void SecureStore.setItemAsync(THEME_STORAGE_KEY, next).catch((error) => {
        console.error('Failed to save appearance preference', error);
      });
    }
  }, []);

  const systemScheme = Platform.OS === 'web' ? webSystemScheme : nativeSystemScheme;
  const resolvedTheme = resolveThemePreference(preference, systemScheme);
  const palette = resolvedTheme === 'dark' ? darkPalette : lightPalette;

  useEffect(() => {
    if (Platform.OS !== 'web' || typeof document === 'undefined') return;
    document.documentElement.style.colorScheme = resolvedTheme;
    document.documentElement.style.backgroundColor = palette.background;
    document.body.style.backgroundColor = palette.background;
  }, [palette.background, resolvedTheme]);

  const value = useMemo<ThemeContextValue>(() => ({
    preference, resolvedTheme, isDark: resolvedTheme === 'dark', isReady,
    palette, shadow: getShadow(palette), setPreference,
  }), [isReady, palette, preference, resolvedTheme, setPreference]);

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useAppTheme() {
  return useContext(ThemeContext);
}

export function createThemedStyles<T extends StyleSheet.NamedStyles<T>>(
  factory: (palette: AppPalette, shadow: ReturnType<typeof getShadow>) => T,
) {
  return function useThemedStyles() {
    const { palette, shadow } = useAppTheme();
    return useMemo(() => StyleSheet.create(factory(palette, shadow)), [palette, shadow]);
  };
}
