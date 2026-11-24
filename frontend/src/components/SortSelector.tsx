import Ionicons from '@expo/vector-icons/Ionicons';
import React, { useState } from 'react';
import {
  Modal,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  View,
  useWindowDimensions,
} from 'react-native';

import { radius, spacing } from '../theme';
import { createThemedStyles, useAppTheme } from '../context/ThemeContext';
import { getMobileWebPlatform } from '../services/mobileWeb';

export type SortOption<Value extends string = string> = {
  value: Value;
  label: string;
};

type SortSelectorProps<Value extends string> = {
  value: Value;
  options: readonly SortOption<Value>[];
  onChange: (value: Value) => void;
};

export function shouldUseMobileSortSheet(runtimePlatform: string, width: number, isMobileWeb: boolean) {
  return runtimePlatform !== 'web' || isMobileWeb || width < 520;
}

export function SortSelector<Value extends string>({ value, options, onChange }: SortSelectorProps<Value>) {
  const styles = useStyles();
  const { palette } = useAppTheme();
  const { width } = useWindowDimensions();
  const [visible, setVisible] = useState(false);
  const selected = options.find((option) => option.value === value) ?? options[0];
  const useMobileSheet = shouldUseMobileSortSheet(Platform.OS, width, getMobileWebPlatform() !== null);

  const choose = (nextValue: Value) => {
    onChange(nextValue);
    setVisible(false);
  };

  const optionRows = (mobile = false) => options.map((option) => (
    <Pressable
      key={option.value}
      onPress={() => choose(option.value)}
      accessibilityRole="button"
      accessibilityState={{ selected: value === option.value }}
      style={[
        styles.option,
        mobile && styles.mobileOption,
        value === option.value && styles.optionActive,
      ]}
    >
      <Text style={[styles.optionText, value === option.value && styles.optionTextActive]}>
        {option.label}
      </Text>
      {value === option.value && <Ionicons name="checkmark" size={18} color={palette.primaryDark} />}
    </Pressable>
  ));

  return (
    <View style={styles.wrap}>
      <Pressable
        onPress={() => setVisible((current) => !current)}
        accessibilityRole="button"
        accessibilityLabel={`Ordenar: ${selected?.label ?? ''}`}
        accessibilityState={{ expanded: visible }}
        style={styles.trigger}
      >
        <Ionicons name="swap-vertical-outline" size={16} color={palette.primaryDark} />
        <Text style={styles.triggerText}>{selected?.label}</Text>
        <Ionicons name={visible ? 'chevron-up' : 'chevron-down'} size={14} color={palette.textSoft} />
      </Pressable>

      {visible && !useMobileSheet && (
        <View style={styles.menu}>{optionRows()}</View>
      )}

      <Modal
        transparent
        animationType="fade"
        visible={visible && useMobileSheet}
        onRequestClose={() => setVisible(false)}
        statusBarTranslucent
      >
        <View style={styles.modalRoot}>
          <Pressable
            style={[StyleSheet.absoluteFillObject, styles.backdrop]}
            onPress={() => setVisible(false)}
            accessibilityRole="button"
            accessibilityLabel="Fechar opções de ordenação"
          />
          <View style={[styles.mobileMenu, { width: Math.max(0, Math.min(width - 32, 360)) }]}>
            <Text style={styles.mobileTitle}>Ordenar por</Text>
            {optionRows(true)}
          </View>
        </View>
      </Modal>
    </View>
  );
}

const useStyles = createThemedStyles((palette) => ({
  wrap: { position: 'relative', zIndex: 3, alignSelf: 'flex-start' },
  trigger: { minHeight: 44, flexDirection: 'row', alignItems: 'center', gap: 7, paddingHorizontal: spacing.sm, borderWidth: 1, borderColor: palette.border, borderRadius: radius.sm, backgroundColor: palette.surface },
  triggerText: { color: palette.text, fontSize: 13, fontWeight: '600' },
  menu: { position: 'absolute', top: 49, right: 0, minWidth: 190, padding: 4, borderWidth: 1, borderColor: palette.border, borderRadius: radius.sm, backgroundColor: palette.surface, shadowColor: palette.shadow, shadowOpacity: 0.16, shadowRadius: 12, shadowOffset: { width: 0, height: 6 }, elevation: 8, zIndex: 10 },
  option: { minHeight: 44, paddingHorizontal: spacing.sm, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', borderRadius: radius.sm },
  mobileOption: { minHeight: 52, paddingHorizontal: spacing.md },
  optionActive: { backgroundColor: palette.primarySoft },
  optionText: { color: palette.text, fontSize: 14 },
  optionTextActive: { color: palette.primaryDark, fontWeight: '700' },
  modalRoot: { flex: 1, justifyContent: 'center', alignItems: 'center', paddingHorizontal: spacing.md },
  backdrop: { backgroundColor: palette.overlay },
  mobileMenu: { maxWidth: 360, padding: spacing.sm, borderWidth: 1, borderColor: palette.border, borderRadius: radius.lg, backgroundColor: palette.surface, shadowColor: palette.shadow, shadowOpacity: 0.24, shadowRadius: 20, shadowOffset: { width: 0, height: 10 }, elevation: 18 },
  mobileTitle: { color: palette.textSoft, fontSize: 12, fontWeight: '700', paddingHorizontal: spacing.md, paddingTop: spacing.sm, paddingBottom: spacing.xs, textTransform: 'uppercase', letterSpacing: 0.8 },
}));
