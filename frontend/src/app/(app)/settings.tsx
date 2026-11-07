import { Ionicons } from '@expo/vector-icons';
import { useRouter } from 'expo-router';
import React from 'react';
import { Platform, Pressable, StyleSheet, Text, View } from 'react-native';

import { Screen } from '../../components/Screen';
import { palette, radius, spacing } from '../../theme';

export default function SettingsScreen() {
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

      <View style={styles.note}>
        <Text style={styles.noteLabel}>{'Apar\u00eancia'}</Text>
        <Text style={styles.noteText}>O aplicativo usa o tema claro.</Text>
      </View>
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
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
  note: { marginTop: spacing.xl, paddingTop: spacing.md, borderTopWidth: 1, borderTopColor: palette.border },
  noteLabel: { color: palette.text, fontSize: 14, fontWeight: '600' },
  noteText: { color: palette.textSoft, fontSize: 13, lineHeight: 19, marginTop: spacing.xs },
});
