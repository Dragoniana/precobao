import Ionicons from '@expo/vector-icons/Ionicons';
import React, { useEffect, useState } from 'react';
import { Linking, Pressable, Text, View } from 'react-native';

import { getAvailableAppRelease, formatReleaseSize } from '../config/appReleases';
import { getMobileWebPlatform } from '../services/mobileWeb';
import { radius, spacing } from '../theme';
import { createThemedStyles, useAppTheme } from '../context/ThemeContext';

const DISMISSED_KEY = 'preco-bao:mobile-app-banner-dismissed-v1';

function unavailableMessage(platform: 'android' | 'ios') {
  return platform === 'ios'
    ? 'Versão para iPhone em breve.'
    : 'O APK oficial para Android está sendo preparado.';
}

async function openRelease(url: string | null, onUnavailable: () => void, onError: () => void) {
  if (!url) {
    onUnavailable();
    return;
  }
  try {
    await Linking.openURL(url);
  } catch {
    onError();
  }
}

export function MobileAppDownloadBanner() {
  const styles = useStyles();
  const { palette } = useAppTheme();
  const platform = getMobileWebPlatform();
  const [dismissed, setDismissed] = useState<boolean | null>(null);
  const [status, setStatus] = useState('');

  useEffect(() => {
    try {
      setDismissed(window.localStorage.getItem(DISMISSED_KEY) === 'true');
    } catch {
      setDismissed(false);
    }
  }, []);

  if (!platform || dismissed !== false) return null;
  const release = getAvailableAppRelease(platform);

  const dismiss = () => {
    try {
      window.localStorage.setItem(DISMISSED_KEY, 'true');
    } catch {
      // Keep the banner dismissible for this visit when storage is unavailable.
    }
    setDismissed(true);
  };

  return (
    <View style={styles.banner} accessibilityLabel="Baixe o aplicativo Preço Bão">
      <View style={styles.brandMark}><Text style={styles.brandLetter}>P</Text></View>
      <View style={styles.bannerCopy}>
        <Text style={styles.bannerTitle}>Baixe o app Preço Bão</Text>
        <Text style={styles.bannerDescription}>Compare preços de medicamentos de forma prática. É grátis!</Text>
        {release ? (
          <Text style={styles.releaseMeta}>Versão {release.version}{platform === 'android' && release.sizeBytes ? ` · ${formatReleaseSize(release.sizeBytes)}` : ''}</Text>
        ) : null}
        {status ? <Text accessibilityRole="alert" style={styles.status}>{status}</Text> : null}
        <Pressable
          onPress={() => openRelease(release?.url ?? null, () => setStatus(unavailableMessage(platform)), () => setStatus('Não foi possível iniciar o download. Tente novamente mais tarde.'))}
          accessibilityRole="button"
          accessibilityLabel="Baixar aplicativo"
          style={({ pressed }) => [styles.downloadButton, pressed && styles.pressed]}
        >
          <Ionicons name="download-outline" size={17} color={palette.onPrimary} />
          <Text style={styles.downloadButtonText}>Baixar aplicativo</Text>
        </Pressable>
      </View>
      <Pressable
        onPress={dismiss}
        accessibilityRole="button"
        accessibilityLabel="Fechar aviso de download"
        hitSlop={10}
        style={styles.closeButton}
      >
        <Ionicons name="close" size={19} color={palette.textSoft} />
      </Pressable>
    </View>
  );
}

export function MobileAppDownloadSettings() {
  const styles = useStyles();
  const { palette } = useAppTheme();
  const platform = getMobileWebPlatform();
  const [status, setStatus] = useState('');
  if (!platform) return null;

  const release = getAvailableAppRelease(platform);
  const availability = release
    ? `Versão ${release.version}${platform === 'android' && release.sizeBytes ? ` · ${formatReleaseSize(release.sizeBytes)}` : ''}`
    : unavailableMessage(platform);

  return (
    <View style={styles.settingsCard}>
      <View style={styles.settingsIcon}><Ionicons name="phone-portrait-outline" size={21} color={palette.primaryDark} /></View>
      <View style={styles.settingsCopy}>
        <Text style={styles.settingsTitle}>Nosso aplicativo</Text>
        <Text style={styles.settingsDescription}>Faça o download do nosso app, é grátis!</Text>
        <Text style={styles.releaseMeta}>{availability}</Text>
        {status ? <Text accessibilityRole="alert" style={styles.status}>{status}</Text> : null}
        <Pressable
          onPress={() => openRelease(release?.url ?? null, () => setStatus(unavailableMessage(platform)), () => setStatus('Não foi possível iniciar o download. Tente novamente mais tarde.'))}
          accessibilityRole="button"
          accessibilityLabel="Baixar Preço Bão"
          style={({ pressed }) => [styles.settingsDownloadButton, pressed && styles.pressed]}
        >
          <Ionicons name="download-outline" size={18} color={palette.primaryDark} />
          <Text style={styles.settingsDownloadText}>Baixar Preço Bão</Text>
        </Pressable>
      </View>
    </View>
  );
}

const useStyles = createThemedStyles((palette) => ({
  banner: { width: '100%', flexDirection: 'row', alignItems: 'flex-start', gap: spacing.sm, marginBottom: spacing.md, padding: spacing.md, borderWidth: 1, borderColor: palette.border, borderRadius: radius.md, backgroundColor: palette.surface, shadowColor: palette.shadow, shadowOpacity: 0.06, shadowRadius: 8, shadowOffset: { width: 0, height: 3 }, elevation: 2 },
  brandMark: { width: 36, height: 36, borderRadius: 11, backgroundColor: palette.primary, alignItems: 'center', justifyContent: 'center' },
  brandLetter: { color: palette.onPrimary, fontSize: 20, fontWeight: '900' },
  bannerCopy: { flex: 1, minWidth: 0, gap: 3 },
  bannerTitle: { color: palette.text, fontSize: 14, lineHeight: 19, fontWeight: '800', paddingRight: 18 },
  bannerDescription: { color: palette.textSoft, fontSize: 12, lineHeight: 17 },
  releaseMeta: { color: palette.textSoft, fontSize: 11, lineHeight: 15, marginTop: 2 },
  status: { color: palette.primaryDark, fontSize: 12, lineHeight: 17, marginTop: 3 },
  downloadButton: { alignSelf: 'flex-start', minHeight: 40, marginTop: spacing.xs, paddingHorizontal: spacing.md, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: spacing.xs, borderRadius: radius.sm, backgroundColor: palette.primary },
  downloadButtonText: { color: palette.onPrimary, fontSize: 12, fontWeight: '700' },
  closeButton: { width: 28, height: 28, alignItems: 'center', justifyContent: 'center', marginTop: -5, marginRight: -5 },
  settingsCard: { flexDirection: 'row', alignItems: 'flex-start', gap: spacing.md, marginTop: spacing.lg, padding: spacing.md, borderWidth: 1, borderColor: palette.border, borderRadius: radius.md, backgroundColor: palette.surface },
  settingsIcon: { width: 40, height: 40, alignItems: 'center', justifyContent: 'center', borderRadius: radius.sm, backgroundColor: palette.primarySoft },
  settingsCopy: { flex: 1, minWidth: 0 },
  settingsTitle: { color: palette.text, fontSize: 16, fontWeight: '800' },
  settingsDescription: { color: palette.textSoft, fontSize: 13, lineHeight: 19, marginTop: 3 },
  settingsDownloadButton: { alignSelf: 'flex-start', minHeight: 44, marginTop: spacing.sm, paddingHorizontal: spacing.md, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: spacing.xs, borderWidth: 1, borderColor: palette.primary, borderRadius: radius.sm, backgroundColor: palette.surface },
  settingsDownloadText: { color: palette.primaryDark, fontSize: 13, fontWeight: '700' },
  pressed: { opacity: 0.78 },
}));
