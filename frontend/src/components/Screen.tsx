import { Ionicons } from '@expo/vector-icons';
import { router, usePathname } from 'expo-router';
import React, { ReactNode, useMemo, useState } from 'react';
import {
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleProp,
  StyleSheet,
  Text,
  View,
  ViewStyle,
  useWindowDimensions,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { useSessionContext } from '../context/AppContext';
import { palette, radius, spacing } from '../theme';

type ScreenProps = {
  children: ReactNode;
  hideBottomNav?: boolean;
  scroll?: boolean;
  contentStyle?: StyleProp<ViewStyle>;
  wideContent?: boolean;
};

const navigation = [
  { label: 'Início', path: '/home', icon: 'home-outline', activeIcon: 'home' },
  { label: 'Buscar', path: '/search', icon: 'search-outline', activeIcon: 'search' },
  { label: 'Favoritos', path: '/favorites', icon: 'heart-outline', activeIcon: 'heart' },
  { label: 'Perfil', path: '/profile', icon: 'person-outline', activeIcon: 'person' },
] as const;

export function Screen({ children, hideBottomNav = false, scroll = true, contentStyle, wideContent = false }: ScreenProps) {
  const pathname = usePathname();
  const { width } = useWindowDimensions();
  const insets = useSafeAreaInsets();
  const { sessionMode, currentUserName, logout } = useSessionContext();
  const [sidebarVisible, setSidebarVisible] = useState(false);
  const isWeb = Platform.OS === 'web';
  const isDesktop = isWeb && width >= 900;

  const activeTab = useMemo(() => {
    if (pathname.includes('/favorites')) return 'favorites';
    if (pathname.includes('/profile')) return 'profile';
    if (pathname.includes('/search')) return 'search';
    return 'home';
  }, [pathname]);

  const goTo = (path: string) => {
    setSidebarVisible(false);
    router.push(path as never);
  };

  const handleLogout = () => {
    setSidebarVisible(false);
    logout();
    router.replace('/login');
  };

  const contentStyles = [styles.content, isDesktop && (wideContent ? styles.contentDesktopWide : styles.contentDesktop), contentStyle, !isDesktop && !hideBottomNav && styles.contentWithMobileNav];

  return (
    <View style={styles.root}>
      {isDesktop && !hideBottomNav && (
        <View style={styles.desktopHeader}>
          <View style={[styles.headerInner, wideContent && styles.headerInnerWide]}>
            <Pressable style={styles.brand} onPress={() => goTo('/home')} accessibilityRole="button" accessibilityLabel="Preço Bão, início">
              <View style={styles.brandMark}><Text style={styles.brandLetter}>P</Text></View>
              <View><Text style={styles.brandName}>Preço Bão</Text><Text style={styles.brandCaption}>COMPARADOR DE MEDICAMENTOS</Text></View>
            </Pressable>
            <View style={styles.desktopNavigation}>
              {navigation.filter((item) => item.path !== '/search').map((item) => {
                const active = activeTab === item.path.slice(1);
                return (
                  <Pressable key={item.path} onPress={() => goTo(item.path)} accessibilityRole="button" accessibilityLabel={item.label} accessibilityState={{ selected: active }} style={({ hovered, pressed }) => [styles.desktopNavItem, active && styles.desktopNavItemActive, hovered && styles.desktopNavItemHover, pressed && styles.pressed]}>
                    <Ionicons name={active ? item.activeIcon : item.icon} size={17} color={active ? palette.primaryDark : palette.textSoft} />
                    <Text style={[styles.desktopNavText, active && styles.desktopNavTextActive]}>{item.label}</Text>
                  </Pressable>
                );
              })}
            </View>
            <View style={styles.headerActions}>
              {sessionMode === 'authenticated' ? (
                <Pressable onPress={() => goTo('/profile')} style={styles.accountButton} accessibilityRole="button" accessibilityLabel="Abrir perfil">
                  <Ionicons name="person-circle-outline" size={21} color={palette.primaryDark} />
                  <Text numberOfLines={1} style={styles.accountName}>{currentUserName || 'Minha conta'}</Text>
                </Pressable>
              ) : <Pressable onPress={() => goTo('/login')} style={styles.signInButton}><Text style={styles.signInText}>Entrar</Text></Pressable>}
              <Pressable onPress={() => setSidebarVisible(true)} style={styles.moreButton} accessibilityRole="button" accessibilityLabel="Mais opções"><Ionicons name="ellipsis-horizontal" size={20} color={palette.text} /></Pressable>
            </View>
          </View>
        </View>
      )}

      {scroll ? (
        <ScrollView style={styles.flex} contentContainerStyle={[contentStyles, styles.scrollContent]} showsVerticalScrollIndicator={false}>
          {children}
        </ScrollView>
      ) : <View style={[styles.flex, contentStyles]}>{children}</View>}

      {!hideBottomNav && !isDesktop && (
        <View style={[styles.mobileNavWrapper, { paddingBottom: Math.max(insets.bottom, Platform.OS === 'web' ? 8 : 10) }]}>
          <View style={styles.mobileNavigation}>
            {navigation.map((item) => {
              const active = activeTab === item.path.slice(1);
              return (
                <Pressable key={item.path} accessibilityRole="button" accessibilityLabel={`Abrir ${item.label.toLowerCase()}`} accessibilityState={{ selected: active }} style={({ pressed }) => [styles.mobileNavItem, pressed && styles.pressed]} onPress={() => router.push(item.path as never)}>
                  <Ionicons name={active ? item.activeIcon : item.icon} size={21} color={active ? palette.primary : palette.textSoft} />
                  <Text style={[styles.mobileNavText, active && styles.mobileNavTextActive]}>{item.label}</Text>
                </Pressable>
              );
            })}
            <Pressable accessibilityRole="button" accessibilityLabel="Abrir menu" style={({ pressed }) => [styles.mobileNavItem, pressed && styles.pressed]} onPress={() => setSidebarVisible(true)}>
              <Ionicons name="menu-outline" size={21} color={palette.textSoft} /><Text style={styles.mobileNavText}>Menu</Text>
            </Pressable>
          </View>
        </View>
      )}

      <Modal transparent animationType="fade" visible={sidebarVisible} onRequestClose={() => setSidebarVisible(false)}>
        <View style={styles.modalOverlay}>
          <View style={[styles.sidebar, isDesktop && styles.sidebarDesktop]}>
            <View style={styles.sidebarHeader}>
              <View style={styles.brandMark}><Text style={styles.brandLetter}>P</Text></View>
              <View style={styles.sidebarIdentity}><Text style={styles.sidebarTitle}>Preço Bão</Text><Text style={styles.sidebarSubtitle}>{sessionMode === 'authenticated' ? currentUserName || 'Usuário autenticado' : 'Modo convidado'}</Text></View>
              <Pressable onPress={() => setSidebarVisible(false)} accessibilityRole="button" accessibilityLabel="Fechar menu" style={styles.closeButton}><Ionicons name="close" size={20} color={palette.text} /></Pressable>
            </View>
            <Text style={styles.menuSectionLabel}>NAVEGAR</Text>
            <View style={styles.sidebarMenu}>
              {[
                ['Home', '/home', 'home-outline'], ['Buscar', '/search', 'search-outline'], ['Favoritos', '/favorites', 'heart-outline'], ['Perfil', '/profile', 'person-outline'], ['Farmácias participantes', '/included-pharmacies', 'storefront-outline'], ['Configurações', '/settings', 'settings-outline'], ['Sobre', '/about', 'information-circle-outline'],
              ].map(([label, path, icon]) => (
                <Pressable key={path} accessibilityRole="button" style={styles.sidebarItem} onPress={() => goTo(path)}>
                  <Ionicons name={icon as never} size={19} color={palette.primaryDark} /><Text style={styles.sidebarItemText}>{label}</Text><Ionicons name="chevron-forward" size={15} color={palette.muted} />
                </Pressable>
              ))}
              {sessionMode === 'authenticated' ? (
                <Pressable accessibilityRole="button" style={[styles.sidebarItem, styles.logoutItem]} onPress={handleLogout}><Ionicons name="log-out-outline" size={19} color={palette.danger} /><Text style={[styles.sidebarItemText, styles.logoutText]}>Sair da conta</Text></Pressable>
              ) : <Pressable accessibilityRole="button" style={styles.sidebarItem} onPress={() => goTo('/login')}><Ionicons name="log-in-outline" size={19} color={palette.primaryDark} /><Text style={styles.sidebarItemText}>Fazer login</Text></Pressable>}
            </View>
          </View>
          <Pressable style={styles.modalBackdrop} onPress={() => setSidebarVisible(false)} accessibilityLabel="Fechar menu" />
        </View>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: palette.background },
  flex: { flex: 1 },
  desktopHeader: { width: '100%', backgroundColor: palette.surface, borderBottomWidth: 1, borderBottomColor: palette.border },
  headerInner: { width: '100%', maxWidth: 1360, minHeight: 76, alignSelf: 'center', paddingHorizontal: 30, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 28 },
  headerInnerWide: { maxWidth: '100%', paddingHorizontal: 24 },
  brand: { flexDirection: 'row', alignItems: 'center', gap: 11, minWidth: 220 },
  brandMark: { width: 38, height: 38, borderRadius: 11, backgroundColor: palette.primary, justifyContent: 'center', alignItems: 'center' },
  brandLetter: { color: palette.surface, fontSize: 23, fontWeight: '900' },
  brandName: { color: palette.text, fontSize: 17, fontWeight: '900', letterSpacing: -0.3 },
  brandCaption: { color: palette.textSoft, fontSize: 9, fontWeight: '700', letterSpacing: 0.8, marginTop: 1 },
  desktopNavigation: { flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 5 },
  desktopNavItem: { minHeight: 42, flexDirection: 'row', alignItems: 'center', gap: 8, paddingHorizontal: 13, borderRadius: radius.sm },
  desktopNavItemActive: { backgroundColor: palette.surfaceBlue },
  desktopNavItemHover: { backgroundColor: palette.surfaceAlt },
  desktopNavText: { color: palette.textSoft, fontSize: 14, fontWeight: '600' },
  desktopNavTextActive: { color: palette.primaryDark },
  headerActions: { minWidth: 200, flexDirection: 'row', alignItems: 'center', justifyContent: 'flex-end', gap: 11 },
  accountButton: { maxWidth: 175, flexDirection: 'row', alignItems: 'center', gap: 7, padding: 8 },
  accountName: { color: palette.text, fontSize: 14, fontWeight: '600', maxWidth: 135 },
  signInButton: { paddingHorizontal: 18, paddingVertical: 10, borderRadius: radius.sm, backgroundColor: palette.primary },
  signInText: { color: palette.surface, fontWeight: '700', fontSize: 14 },
  moreButton: { width: 38, height: 38, borderRadius: radius.sm, backgroundColor: palette.surfaceAlt, justifyContent: 'center', alignItems: 'center' },
  content: { width: '100%', maxWidth: 720, alignSelf: 'center', paddingHorizontal: spacing.md, paddingTop: spacing.lg, paddingBottom: spacing.xl },
  contentDesktop: { maxWidth: 1320, paddingHorizontal: 32, paddingTop: 30, paddingBottom: 48 },
  contentDesktopWide: { maxWidth: '100%', paddingHorizontal: 24, paddingTop: 30, paddingBottom: 48 },
  contentWithMobileNav: { paddingBottom: 80 },
  scrollContent: { flexGrow: 1 },
  mobileNavWrapper: { width: '100%', backgroundColor: palette.surface, borderTopWidth: 1, borderTopColor: palette.border, paddingTop: 7, paddingBottom: Platform.OS === 'web' ? 8 : 12 },
  mobileNavigation: { width: '100%', maxWidth: 560, alignSelf: 'center', flexDirection: 'row', justifyContent: 'space-around', alignItems: 'center', paddingHorizontal: 8 },
  mobileNavItem: { minWidth: 58, minHeight: 48, alignItems: 'center', justifyContent: 'center', gap: 2, borderRadius: radius.sm, paddingHorizontal: 5 },
  mobileNavText: { color: palette.textSoft, fontSize: 10, fontWeight: '600' },
  mobileNavTextActive: { color: palette.primaryDark, fontWeight: '800' },
  pressed: { opacity: 0.72 },
  modalOverlay: { flex: 1, flexDirection: 'row' },
  modalBackdrop: { flex: 1, backgroundColor: 'rgba(15,23,42,0.32)' },
  sidebar: { width: 310, maxWidth: '88%', backgroundColor: palette.surface, paddingTop: 24, paddingHorizontal: 18, paddingBottom: 20 },
  sidebarDesktop: { width: 340 },
  sidebarHeader: { flexDirection: 'row', alignItems: 'center', gap: 11, marginBottom: 28 },
  sidebarIdentity: { flex: 1 },
  sidebarTitle: { color: palette.text, fontSize: 17, fontWeight: '900' },
  sidebarSubtitle: { marginTop: 2, color: palette.textSoft, fontSize: 12 },
  closeButton: { width: 36, height: 36, borderRadius: radius.sm, backgroundColor: palette.surfaceAlt, alignItems: 'center', justifyContent: 'center' },
  menuSectionLabel: { color: palette.muted, fontSize: 10, fontWeight: '800', letterSpacing: 1, marginBottom: 10, paddingHorizontal: 7 },
  sidebarMenu: { gap: 5 },
  sidebarItem: { minHeight: 46, paddingHorizontal: 11, borderRadius: radius.sm, flexDirection: 'row', alignItems: 'center', gap: 11, backgroundColor: palette.surface },
  sidebarItemText: { flex: 1, color: palette.text, fontSize: 13, fontWeight: '700' },
  logoutItem: { marginTop: 8, borderTopWidth: 1, borderColor: palette.border, borderRadius: 0 },
  logoutText: { color: palette.danger },
});
