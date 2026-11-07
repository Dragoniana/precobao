import Ionicons from '@expo/vector-icons/Ionicons';
import { useRouter } from 'expo-router';
import React from 'react';
import { Platform, Pressable, Text, View } from 'react-native';

import { Screen } from '../../components/Screen';
import { radius, spacing } from '../../theme';
import { createThemedStyles, useAppTheme } from '../../context/ThemeContext';

export default function SettingsScreen() {
  const styles = useStyles();
  const { palette, preference, resolvedTheme, setPreference } = useAppTheme();
  const router = useRouter();
  return (
    <Screen>
      <View style={[styles.content, Platform.OS === 'web' && styles.contentWeb]}>
      <Text style={styles.eyebrow}>CONTA E APLICATIVO</Text>
      <Text style={styles.title}>Configurações</Text>
      <Text style={styles.subtitle}>Gerencie os dados da sua conta.</Text>

      <Pressable accessibilityRole="button" onPress={() => router.push('/(app)/profile' as never)} style={({ pressed }) => [styles.row, pressed && styles.pressed]}>
        <View style={styles.icon}><Ionicons name="person-outline" size={20} color={palette.primary} /></View>
        <View style={styles.copy}>
          <Text style={styles.label}>Dados da conta</Text>
          <Text style={styles.description}>Edite seu nome e e-mail</Text>
        </View>
        <Ionicons name="chevron-forward" size={18} color={palette.textSoft} />
      </Pressable>

      <View style={styles.appearanceSection}>
        <Text style={styles.sectionTitle}>{'Apar\u00eancia'}</Text>
        <Text style={styles.sectionDescription}>
          Escolha como o Preço Bão aparece neste dispositivo.
        </Text>
        <View accessibilityRole="radiogroup" style={styles.themeOptions}>
          {([
            { value: 'light', label: 'Claro', icon: 'sunny-outline' },
            { value: 'dark', label: 'Escuro', icon: 'moon-outline' },
            { value: 'system', label: 'Sistema', icon: 'phone-portrait-outline' },
          ] as const).map((option) => {
            const selected = preference === option.value;
            return (
              <Pressable
                key={option.value}
                accessibilityRole="radio"
                accessibilityState={{ checked: selected }}
                accessibilityLabel={`Tema ${option.label}`}
                onPress={() => setPreference(option.value)}
                style={({ pressed }) => [
                  styles.themeOption,
                  selected && styles.themeOptionSelected,
                  pressed && styles.pressed,
                ]}
              >
                <Ionicons
                  name={option.icon}
                  size={20}
                  color={selected ? palette.primaryDark : palette.textSoft}
                />
                <Text style={[styles.themeOptionLabel, selected && styles.themeOptionLabelSelected]}>
                  {option.label}
                </Text>
                <View style={[styles.radio, selected && styles.radioSelected]}>
                  {selected ? <View style={styles.radioDot} /> : null}
                </View>
              </Pressable>
            );
          })}
        </View>
        {preference === 'system' ? (
          <Text style={styles.systemStatus}>
            Seguindo o sistema: tema {resolvedTheme === 'dark' ? 'escuro' : 'claro'}.
          </Text>
        ) : null}
      </View>
      </View>
    </Screen>
  );
}

const useStyles = createThemedStyles((palette) => ({
  content: { width: '100%' },
  contentWeb: { maxWidth: 840, alignSelf: 'center' },
  eyebrow: { color: palette.primaryDark, fontSize: 11, fontWeight: '800', letterSpacing: 1.1, marginTop: spacing.md },
  title: { color: palette.text, fontSize: 28, fontWeight: '800', marginTop: spacing.xs },
  subtitle: { color: palette.textSoft, fontSize: 14, lineHeight: 21, marginTop: spacing.xs, marginBottom: spacing.lg },
  row: { minHeight: 76, backgroundColor: palette.surface, borderRadius: radius.md, paddingHorizontal: spacing.md, flexDirection: 'row', alignItems: 'center', gap: spacing.md },
  icon: { width: 40, height: 40, borderRadius: radius.sm, backgroundColor: palette.primarySoft, alignItems: 'center', justifyContent: 'center' },
  copy: { flex: 1 },
  label: { color: palette.text, fontSize: 15, fontWeight: '700' },
  description: { color: palette.textSoft, fontSize: 13, marginTop: 3 },
  pressed: { opacity: 0.75 },
  appearanceSection: { marginTop: spacing.xl, paddingTop: spacing.lg, borderTopWidth: 1, borderTopColor: palette.border },
  sectionTitle: { color: palette.text, fontSize: 18, lineHeight: 24, fontWeight: '700' },
  sectionDescription: { color: palette.textSoft, fontSize: 13, lineHeight: 19, marginTop: 4, marginBottom: spacing.md },
  themeOptions: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  themeOption: { minWidth: 150, flexGrow: 1, minHeight: 54, paddingHorizontal: spacing.md, borderRadius: radius.md, borderWidth: 1, borderColor: palette.border, backgroundColor: palette.surface, flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  themeOptionSelected: { borderColor: palette.primary, backgroundColor: palette.primarySoft },
  themeOptionLabel: { flex: 1, color: palette.text, fontSize: 14, fontWeight: '600' },
  themeOptionLabelSelected: { color: palette.primaryDark },
  radio: { width: 18, height: 18, borderRadius: 9, borderWidth: 2, borderColor: palette.muted, alignItems: 'center', justifyContent: 'center' },
  radioSelected: { borderColor: palette.primary },
  radioDot: { width: 8, height: 8, borderRadius: 4, backgroundColor: palette.primary },
  systemStatus: { color: palette.textSoft, fontSize: 12, lineHeight: 18, marginTop: spacing.sm },
}));
