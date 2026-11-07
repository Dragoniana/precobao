import { useAppTheme } from '@/context/ThemeContext';

export function useTheme() {
  return useAppTheme().palette;
}
