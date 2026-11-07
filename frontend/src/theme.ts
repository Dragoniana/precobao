export const lightPalette = {
  background: '#F6F8FC',
  backgroundSoft: '#EEF4FF',
  surface: '#FFFFFF',
  surfaceAlt: '#F1F5F9',
  surfaceBlue: '#EAF4FF',
  primary: '#1688F6',
  primaryDark: '#0B64C0',
  primarySoft: '#DCEEFF',
  text: '#172033',
  textSoft: '#667085',
  border: '#D9E6F7',
  muted: '#98A2B3',
  danger: '#C93636', success: '#25883E', warning: '#A86200',
  dangerSoft: '#FFF1F1', successSoft: '#EAF8EF', warningSoft: '#FFF8E8',
  warningBorder: '#F5D48A', overlay: 'rgba(15, 23, 42, 0.48)',
  imageSurface: '#FFFFFF', chartBackground: '#F8FBFF', chartGrid: '#D8E3F1',
  shadow: '#0F172A', onPrimary: '#FFFFFF', favorite: '#E14747',
} as const;

export type AppPalette = { [K in keyof typeof lightPalette]: string };
export type ResolvedTheme = 'light' | 'dark';
export type ThemePreference = ResolvedTheme | 'system';

export const darkPalette: AppPalette = {
  background: '#101722', backgroundSoft: '#142033', surface: '#1B2635',
  surfaceAlt: '#253246', surfaceBlue: '#17365A', primary: '#3B91FF',
  primaryDark: '#78B7FF', primarySoft: '#183B63', text: '#F2F6FC',
  textSoft: '#B7C4D6', border: '#3A4960', muted: '#93A3B8',
  danger: '#FF8A8A', success: '#74D68B', warning: '#FFC46B',
  dangerSoft: '#4A252B', successSoft: '#183D2A', warningSoft: '#49371F',
  warningBorder: '#795B2C', overlay: 'rgba(2, 7, 15, 0.76)',
  imageSurface: '#F8FAFC', chartBackground: '#152131', chartGrid: '#34445A',
  shadow: '#02060C', onPrimary: '#F8FBFF', favorite: '#FF8A8A',
};

// Non-visual utilities can retain this compatibility export. UI uses useAppTheme().
export const palette: AppPalette = lightPalette;

export const spacing = {
  xs: 6,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
  xxl: 36,
};

export const radius = {
  sm: 8,
  md: 12,
  lg: 16,
  xl: 20,
  pill: 999,
};

export function getShadow(paletteValue: AppPalette) {
  return {
  soft: {
    shadowColor: paletteValue.shadow,
    shadowOpacity: 0.08,
    shadowRadius: 6,
    shadowOffset: {
      width: 0,
      height: 3,
    },
    elevation: 2,
  },
  medium: {
    shadowColor: paletteValue.shadow,
    shadowOpacity: 0.14,
    shadowRadius: 12,
    shadowOffset: {
      width: 0,
      height: 6,
    },
    elevation: 3,
  },
  };
}

export const shadow = getShadow(lightPalette);
