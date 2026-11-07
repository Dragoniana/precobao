import { Pressable, Text } from 'react-native';

import { radius, spacing } from '../theme';
import { createThemedStyles } from '../context/ThemeContext';

type FilterChipProps = {
  label: string;
  active: boolean;
  onPress: () => void;
  accessibilityLabel?: string;
  compact?: boolean;
};

export function FilterChip({ label, active, onPress, accessibilityLabel, compact = false }: FilterChipProps) {
  const styles = useStyles();
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel ?? label}
      accessibilityState={{ selected: active }}
      style={[styles.chip, compact && styles.chipCompact, active && styles.chipActive]}
      onPress={onPress}
    >
      <Text style={[styles.label, compact && styles.labelCompact, active && styles.labelActive]}>{label}</Text>
    </Pressable>
  );
}

const useStyles = createThemedStyles((palette) => ({
  chip: {
    minHeight: 40,
    borderRadius: radius.pill,
    borderWidth: 1,
    borderColor: palette.primary,
    backgroundColor: palette.surface,
    justifyContent: 'center',
    paddingHorizontal: spacing.md,
    marginBottom: spacing.sm,
    maxWidth: '100%',
  },
  chipCompact: {
    alignSelf: 'flex-start',
    flexShrink: 0,
    minHeight: 42,
    paddingVertical: spacing.sm,
    marginBottom: 0,
  },
  chipActive: {
    backgroundColor: palette.primary,
  },
  label: {
    color: palette.text,
    fontSize: 14,
    flexShrink: 1,
  },
  labelCompact: {
    minWidth: 0,
    maxWidth: '100%',
    flexShrink: 1,
    lineHeight: 19,
  },
  labelActive: {
    color: palette.onPrimary,
    fontWeight: '600',
  },
}));
