import { Stack } from 'expo-router';
import Head from 'expo-router/head';
import * as SplashScreen from 'expo-splash-screen';
import { StatusBar } from 'expo-status-bar';
import { useEffect } from 'react';
import { Platform } from 'react-native';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { GlobalButtonHover } from '../components/GlobalButtonHover';
import { AppProvider } from '../context/AppContext';
import { ThemeProvider, useAppTheme } from '../context/ThemeContext';

void SplashScreen.preventAutoHideAsync().catch(() => undefined);

const WEB_DOCUMENT_TITLE = 'precobao.app';
const FAVICON_VERSION = '20261124';

function ThemedApplication() {
  const { isDark, isReady, palette } = useAppTheme();
  useEffect(() => {
    if (isReady) void SplashScreen.hideAsync().catch(() => undefined);
  }, [isReady]);

  if (!isReady) return null;

  return (
    <GestureHandlerRootView style={{ flex: 1, backgroundColor: palette.background }}>
      <SafeAreaProvider>
        <AppProvider>
          <GlobalButtonHover />
          <StatusBar style={isDark ? 'light' : 'dark'} backgroundColor={palette.background} />
          <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: palette.background } }} />
        </AppProvider>
      </SafeAreaProvider>
    </GestureHandlerRootView>
  );
}

export default function RootLayout() {
  useEffect(() => {
    if (Platform.OS !== 'web' || typeof document === 'undefined') return;

    const keepFixedTitle = () => {
      if (document.title !== WEB_DOCUMENT_TITLE) document.title = WEB_DOCUMENT_TITLE;
    };
    keepFixedTitle();

    if (typeof MutationObserver === 'undefined' || !document.head) return;
    const observer = new MutationObserver(keepFixedTitle);
    observer.observe(document.head, { childList: true, subtree: true, characterData: true });
    return () => observer.disconnect();
  }, []);

  return (
    <>
      {Platform.OS === 'web' ? (
        <Head>
          <title>{WEB_DOCUMENT_TITLE}</title>
          <link rel="icon" type="image/x-icon" href={`/favicon.ico?v=${FAVICON_VERSION}`} />
          <link rel="icon" type="image/png" sizes="16x16" href={`/favicon-16x16.png?v=${FAVICON_VERSION}`} />
          <link rel="icon" type="image/png" sizes="32x32" href={`/favicon-32x32.png?v=${FAVICON_VERSION}`} />
          <link rel="apple-touch-icon" sizes="180x180" href={`/apple-touch-icon.png?v=${FAVICON_VERSION}`} />
        </Head>
      ) : null}
      <ThemeProvider>
        <ThemedApplication />
      </ThemeProvider>
    </>
  );
}
