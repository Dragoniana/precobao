import { Ionicons } from '@expo/vector-icons';
import { DrawerContentComponentProps, DrawerContentScrollView, DrawerItem } from '@react-navigation/drawer';
import { usePathname, useRouter } from 'expo-router';
import { StyleSheet, Text, View } from 'react-native';

import { palette, radius, spacing } from '../theme';

const drawerLinks = [
  { label: 'Favoritos', route: '/favorites', icon: 'heart-outline' },
  { label: 'Farmácias inclusas', route: '/included-pharmacies', icon: 'add-outline' },
  { label: 'Settings', route: '/settings', icon: 'settings-outline' },
  { label: 'Saiba mais', route: '/about', icon: 'log-out-outline' },
] as const;

export function AppDrawerContent(props: DrawerContentComponentProps) {
  const pathname = usePathname();
  const router = useRouter();

  return (
    <DrawerContentScrollView {...props} contentContainerStyle={styles.container}>
      <View style={styles.avatarRow}>
        <View style={styles.avatarBox}>
          <Text style={styles.avatarText}>P</Text>
        </View>
        <Text style={styles.brand}>Preço Bão</Text>
      </View>

      <View style={styles.itemsWrapper}>
        {drawerLinks.map((item) => {
          const active = pathname === item.route;

          return (
            <DrawerItem
              key={item.route}
              label={item.label}
              onPress={() => {
                props.navigation.closeDrawer();
                router.push(item.route);
              }}
              icon={({ color, size }) => (
                <Ionicons name={item.icon as any} size={size} color={active ? palette.primary : color} />
              )}
              labelStyle={[styles.label, active && styles.labelActive]}
              style={[styles.item, active && styles.itemActive]}
            />
          );
        })}
      </View>
    </DrawerContentScrollView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    paddingTop: spacing.md,
  },
  avatarRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    paddingHorizontal: spacing.md,
    marginBottom: spacing.md,
  },
  avatarBox: {
    width: 30,
    height: 30,
    borderRadius: radius.sm,
    backgroundColor: palette.primary,
    justifyContent: 'center',
    alignItems: 'center',
  },
  avatarText: {
    color: palette.surface,
    fontWeight: '700',
  },
  brand: {
    fontSize: 16,
    color: palette.primary,
    fontWeight: '700',
  },
  itemsWrapper: {
    marginTop: spacing.md,
  },
  item: {
    borderRadius: radius.sm,
    marginHorizontal: spacing.sm,
  },
  itemActive: {
    backgroundColor: '#D3E9FF',
  },
  label: {
    color: palette.text,
    fontSize: 16,
    marginLeft: -8,
  },
  labelActive: {
    color: palette.primary,
    fontWeight: '700',
  },
});
