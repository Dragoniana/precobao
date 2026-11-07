import { Stack } from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import { StatusBar } from 'expo-status-bar';
import { useEffect } from 'react';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { GlobalButtonHover } from '../components/GlobalButtonHover';
import { AppProvider } from '../context/AppContext';
import { ThemeProvider, useAppTheme } from '../context/ThemeContext';

void SplashScreen.preventAutoHideAsync().catch(() => undefined);

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
  return (
    <ThemeProvider>
      <ThemedApplication />
    </ThemeProvider>
  );
}
