import { parseThemePreference, resolveThemePreference, THEME_STORAGE_KEY } from '../context/ThemeContext';
import { darkPalette, lightPalette } from '../theme';

describe('appearance preferences', () => {
  it.each(['light', 'dark', 'system'] as const)('accepts the saved %s preference', (preference) => {
    expect(parseThemePreference(preference)).toBe(preference);
  });

  it('defaults new and invalid installations to the system preference', () => {
    expect(parseThemePreference(null)).toBe('system');
    expect(parseThemePreference('legacy-value')).toBe('system');
    expect(THEME_STORAGE_KEY).toBe('preco-bao-appearance');
  });

  it('follows both system appearances when automatic mode is selected', () => {
    expect(resolveThemePreference('system', 'light')).toBe('light');
    expect(resolveThemePreference('system', 'dark')).toBe('dark');
  });

  it('keeps manual preferences fixed when the system changes', () => {
    expect(resolveThemePreference('light', 'dark')).toBe('light');
    expect(resolveThemePreference('dark', 'light')).toBe('dark');
  });

  it('provides distinct semantic palettes with readable product-image surfaces', () => {
    expect(darkPalette.background).not.toBe(lightPalette.background);
    expect(darkPalette.text).not.toBe(lightPalette.text);
    expect(darkPalette.primary).not.toBe(lightPalette.primary);
    expect(darkPalette.imageSurface).toMatch(/^#/);
    expect(darkPalette.chartGrid).not.toBe(lightPalette.chartGrid);
  });
});
