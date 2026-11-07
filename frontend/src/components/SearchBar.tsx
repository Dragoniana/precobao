import Ionicons from '@expo/vector-icons/Ionicons';
import React, { useState } from 'react';
import {
  Platform,
  Pressable,
  Text,
  TextInput,
  TextInputProps,
  useWindowDimensions,
  View,
} from 'react-native';

import { radius, spacing } from '../theme';
import { createThemedStyles, useAppTheme } from '../context/ThemeContext';

type SearchBarProps = {
  value: string;
  onChangeText: (value: string) => void;
  onSubmit?: () => void;
  onBack?: () => void;
  onFilter?: () => void;
  filterCount?: number;
  placeholder?: string;
} & Omit<TextInputProps, 'value' | 'onChangeText'>;

export function SearchBar({
  value,
  onChangeText,
  onSubmit,
  onBack,
  onFilter,
  filterCount = 0,
  placeholder = 'Buscar medicamento ou princípio ativo',
  ...textInputProps
}: SearchBarProps) {
  const styles = useStyles();
  const { palette } = useAppTheme();
  const isWeb = Platform.OS === 'web';
  const { width } = useWindowDimensions();
  const isCompact = width <= 480;
  const responsivePlaceholder = onBack && isCompact && placeholder === 'Buscar medicamento ou princípio ativo'
    ? 'Medicamento ou princípio ativo'
    : placeholder;

  const [focused, setFocused] = useState(false);

  return (
    <View style={[styles.row, onFilter && isCompact && styles.rowCompact]}>
      {onBack ? (
        <Pressable accessibilityRole="button" accessibilityLabel="Voltar" style={styles.backButton} onPress={onBack}>
          <Ionicons name="chevron-back" size={25} color={palette.text} />
        </Pressable>
      ) : null}

      <View
        style={[
          styles.container,
          isWeb && styles.containerWeb,
          focused && styles.containerFocused,
        ]}
      >
        <Ionicons name="search" size={21} color={palette.primary} />

        <TextInput
          value={value}
          onChangeText={onChangeText}
          placeholder={responsivePlaceholder}
          placeholderTextColor={palette.muted}
          returnKeyType="search"
          onSubmitEditing={onSubmit}
          style={[styles.input, isWeb && styles.inputWeb]}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          {...textInputProps}
        />

        {onFilter && !isCompact ? (
          <Pressable accessibilityRole="button" accessibilityLabel="Abrir filtros" style={styles.filterButton} onPress={onFilter}>
            <Ionicons name="options-outline" size={18} color={palette.surface} />
            <Text style={styles.filterText}>{filterCount ? `Filtros (${filterCount})` : 'Filtros'}</Text>
          </Pressable>
        ) : null}
      </View>
      {onFilter && isCompact ? (
        <View style={styles.filterButtonRow}>
          <Pressable accessibilityRole="button" accessibilityLabel={filterCount ? `Abrir filtros, ${filterCount} selecionados` : 'Abrir filtros'} style={styles.filterButtonMobile} onPress={onFilter}>
            <Ionicons name="options-outline" size={17} color={palette.primaryDark} />
            <Text style={styles.filterTextMobile}>{filterCount ? `Filtros (${filterCount})` : 'Filtros'}</Text>
          </Pressable>
        </View>
      ) : null}
    </View>
  );
}

const useStyles = createThemedStyles((palette, shadow) => ({
  row: {
    width: '100%',
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
  },

  rowCompact: {
    flexDirection: 'column',
    alignItems: 'stretch',
    gap: spacing.xs,
  },

  backButton: {
    width: 42,
    height: 42,
    borderRadius: radius.pill,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: palette.surface,
    borderWidth: 1,
    borderColor: palette.border,
  },

  container: {
    flex: 1,
    minHeight: 52,
    borderWidth: 1,
    borderColor: palette.border,
    borderRadius: radius.pill,
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    paddingLeft: spacing.md,
    paddingRight: spacing.xs,
    backgroundColor: palette.surface,
    ...shadow.soft,
  },

  containerWeb: {
    minHeight: 60,
    paddingLeft: spacing.lg,
    paddingRight: spacing.sm,
  },

  containerFocused: {
    borderColor: palette.primary,
  },

  input: {
    flex: 1,
    minWidth: 0,
    flexShrink: 1,
    color: palette.text,
    fontSize: 16,
    paddingVertical: spacing.sm,
  },

  inputWeb: {
    fontSize: 17,
    outlineWidth: 0,
    outlineColor: 'transparent',
    outlineStyle: 'none' as any,
  },

  filterButton: {
    minHeight: 40,
    minWidth: 42,
    borderRadius: radius.pill,
    backgroundColor: palette.primary,
    paddingHorizontal: spacing.md,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: spacing.xs,
  },

  filterButtonRow: {
    width: '100%',
    alignItems: 'flex-end',
  },

  filterButtonMobile: {
    minHeight: 44,
    minWidth: 104,
    paddingHorizontal: spacing.md,
    borderRadius: radius.pill,
    borderWidth: 1,
    borderColor: palette.border,
    backgroundColor: palette.surface,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: spacing.xs,
  },

  filterTextMobile: {
    color: palette.primaryDark,
    fontSize: 14,
    fontWeight: '700',
  },

  filterText: {
    color: palette.onPrimary,
    fontSize: 14,
    fontWeight: '800',
  },
}));
