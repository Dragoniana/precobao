import Ionicons from '@expo/vector-icons/Ionicons';
import { useNavigation } from '@react-navigation/native';
import { usePathname, useRouter } from 'expo-router';
import { Platform, Pressable, View } from 'react-native';

import { radius, spacing } from '../theme';
import { createThemedStyles, useAppTheme } from '../context/ThemeContext';

export function BottomNav() {
  const styles = useStyles();
  const { palette } = useAppTheme();
  const router = useRouter();
  const navigation = useNavigation<any>();
  const pathname = usePathname();
  const isWeb = Platform.OS === 'web';

  const homeActive =
    pathname === '/home' ||
    pathname.startsWith('/medicine') ||
    pathname.startsWith('/history') ||
    pathname === '/search' ||
    pathname === '/filters';

  const menuActive =
    pathname === '/favorites' ||
    pathname === '/included-pharmacies' ||
    pathname === '/settings' ||
    pathname === '/about';

  const profileActive = pathname === '/profile';

  return (
    <View style={[styles.wrapper, isWeb ? styles.wrapperWeb : styles.wrapperMobile]}>
      <Pressable
        style={[styles.iconButton, menuActive && styles.iconButtonActive]}
        onPress={() => navigation.openDrawer?.()}
      >
        <Ionicons
          name="menu"
          size={20}
          color={menuActive ? palette.primary : palette.textSoft}
        />
      </Pressable>

      <Pressable
        style={[styles.homeButton, homeActive && styles.homeButtonActive]}
        onPress={() => router.replace('/home')}
      >
        <Ionicons
          name="home-outline"
          size={22}
          color={homeActive ? palette.surface : palette.textSoft}
        />
      </Pressable>

      <Pressable
        style={[styles.iconButton, profileActive && styles.iconButtonActive]}
        onPress={() => router.replace('/profile')}
      >
        <Ionicons
          name="person-outline"
          size={20}
          color={profileActive ? palette.primary : palette.textSoft}
        />
      </Pressable>
    </View>
  );
}

const useStyles = createThemedStyles((palette) => ({
  wrapper: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    backgroundColor: palette.background,
  },
  wrapperMobile: {
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.sm,
    paddingBottom: spacing.md,
    borderTopWidth: 1,
    borderTopColor: palette.border,
  },
  wrapperWeb: {
    width: '100%',
    maxWidth: 260,
    alignSelf: 'center',
    marginTop: 8,
    marginBottom: 16,
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderWidth: 1,
    borderColor: palette.border,
    borderRadius: radius.pill,
    backgroundColor: palette.surface,
  },
  iconButton: {
    width: 34,
    height: 34,
    borderRadius: radius.pill,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: palette.surfaceAlt,
  },
  iconButtonActive: {
    backgroundColor: palette.primarySoft,
  },
  homeButton: {
    width: 46,
    height: 46,
    borderRadius: radius.pill,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: palette.surfaceAlt,
  },
  homeButtonActive: {
    backgroundColor: palette.primary,
  },
}));
