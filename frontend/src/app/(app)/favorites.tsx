import Ionicons from '@expo/vector-icons/Ionicons';
import { useRouter } from 'expo-router';
import React from 'react';
import { ActivityIndicator, Pressable, Text, useWindowDimensions, View } from 'react-native';

import { MedicineCard } from '../../components/MedicineCard';
import { Screen } from '../../components/Screen';
import { markMedicineOpenTrace, startMedicineOpenTrace } from '../../services/medicineOpenPerformance';
import { useCatalogContext, useFavoritesContext, useSessionContext } from '../../context/AppContext';
import { radius } from '../../theme';
import { createThemedStyles, useAppTheme } from '../../context/ThemeContext';

export default function FavoritesScreen() {
  const styles = useStyles();
  const { palette } = useAppTheme();
  const { width } = useWindowDimensions();
  const router = useRouter();
  const { favoriteMedicines, favoritesLoading, favoritesError, refreshFavorites } = useFavoritesContext();
  const { markAsViewed } = useCatalogContext();
  const { sessionMode } = useSessionContext();
  const authenticated = sessionMode === 'authenticated';

  return (
    <Screen>
      <View style={styles.heading}>
        <View style={styles.headingCopy}>
          <Text style={styles.eyebrow}>SUA LISTA PESSOAL</Text>
          <Text style={styles.title}>Favoritos</Text>
          <Text style={styles.subtitle}>Acompanhe os preços dos medicamentos que você salvou.</Text>
        </View>
        {authenticated && <View style={styles.counter}><Text style={styles.counterValue}>{favoriteMedicines.length}</Text><Text style={styles.counterLabel}>salvos</Text></View>}
      </View>

      {!authenticated ? (
        <View style={styles.stateCard}>
          <View style={styles.stateIcon}><Ionicons name="heart-outline" size={24} color={palette.primary} /></View>
          <Text style={styles.stateTitle}>Entre para acessar seus favoritos</Text>
          <Text style={styles.stateText}>Sua lista fica salva na conta e pode ser consultada quando você entrar novamente.</Text>
          <Pressable style={styles.primaryButton} onPress={() => router.push('/login' as never)} accessibilityRole="button"><Text style={styles.primaryButtonText}>Fazer login</Text></Pressable>
        </View>
      ) : favoritesLoading ? (
        <View style={styles.stateCard}><ActivityIndicator color={palette.primary} /><Text style={styles.stateText}>Sincronizando seus favoritos…</Text></View>
      ) : favoritesError ? (
        <View style={styles.stateCard}><Text style={styles.stateTitle}>Não foi possível carregar os favoritos</Text><Text style={styles.stateText}>{favoritesError}</Text><Pressable style={styles.secondaryButton} onPress={() => { void refreshFavorites(); }} accessibilityRole="button"><Text style={styles.secondaryButtonText}>Tentar novamente</Text></Pressable></View>
      ) : favoriteMedicines.length === 0 ? (
        <View style={styles.stateCard}>
          <View style={styles.stateIcon}><Ionicons name="heart-outline" size={24} color={palette.primary} /></View>
          <Text style={styles.stateTitle}>Sua lista está vazia</Text>
          <Text style={styles.stateText}>Salve um medicamento pelo ícone de coração para acompanhar seu menor preço aqui.</Text>
          <Pressable style={styles.primaryButton} onPress={() => router.push('/home' as never)} accessibilityRole="button"><Ionicons name="search-outline" size={17} color={palette.surface} /><Text style={styles.primaryButtonText}>Buscar medicamentos</Text></Pressable>
        </View>
      ) : (
        <View testID="favorites-grid" style={[styles.grid, width <= 480 && styles.gridMobileList, favoriteMedicines.length === 1 && styles.singleFavorite]}>
          {favoriteMedicines.map((medicine) => <MedicineCard key={medicine.id} medicine={medicine} compact wide={favoriteMedicines.length === 1} onPress={() => { startMedicineOpenTrace(medicine.id, 'favorites'); markAsViewed(medicine.id); markMedicineOpenTrace(medicine.id, 'navigation-start'); router.push(`/medicine/${medicine.id}` as never); }} />)}
        </View>
      )}
    </Screen>
  );
}

const useStyles = createThemedStyles((palette) => ({
  heading: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24, paddingBottom: 20, borderBottomWidth: 1, borderBottomColor: palette.border },
  headingCopy: { flex: 1 },
  eyebrow: { color: palette.primaryDark, fontSize: 10, fontWeight: '800', letterSpacing: 1 },
  title: { marginTop: 5, color: palette.text, fontSize: 29, lineHeight: 35, fontWeight: '900' },
  subtitle: { marginTop: 5, color: palette.textSoft, fontSize: 14, lineHeight: 21 },
  counter: { minWidth: 74, paddingHorizontal: 13, paddingVertical: 9, alignItems: 'center', borderRadius: radius.md, backgroundColor: palette.surfaceBlue },
  counterValue: { color: palette.primaryDark, fontSize: 19, fontWeight: '900' },
  counterLabel: { marginTop: 1, color: palette.textSoft, fontSize: 10, fontWeight: '700' },
  grid: { flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'space-between', rowGap: 16 },
  gridMobileList: { flexDirection: 'column', flexWrap: 'nowrap', alignItems: 'stretch', justifyContent: 'flex-start', rowGap: 10 },
  singleFavorite: { width: '100%', maxWidth: 820, justifyContent: 'flex-start' },
  stateCard: { width: '100%', maxWidth: 600, minHeight: 230, alignSelf: 'center', alignItems: 'center', justifyContent: 'center', padding: 28, borderRadius: radius.md, borderWidth: 1, borderColor: palette.border, backgroundColor: palette.surface, gap: 10 },
  stateIcon: { width: 48, height: 48, borderRadius: 14, alignItems: 'center', justifyContent: 'center', backgroundColor: palette.surfaceBlue },
  stateTitle: { color: palette.text, fontSize: 17, fontWeight: '800', textAlign: 'center' },
  stateText: { maxWidth: 440, color: palette.textSoft, fontSize: 13, lineHeight: 19, textAlign: 'center' },
  primaryButton: { minHeight: 42, marginTop: 7, paddingHorizontal: 17, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, borderRadius: radius.sm, backgroundColor: palette.primary },
  primaryButtonText: { color: palette.onPrimary, fontSize: 13, fontWeight: '800' },
  secondaryButton: { marginTop: 7, minHeight: 40, justifyContent: 'center', paddingHorizontal: 16, borderRadius: radius.sm, borderWidth: 1, borderColor: palette.primary },
  secondaryButtonText: { color: palette.primaryDark, fontWeight: '800', fontSize: 13 },
}));
