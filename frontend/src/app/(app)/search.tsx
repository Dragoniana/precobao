import { Ionicons } from '@expo/vector-icons';
import { useLocalSearchParams, useRouter } from 'expo-router';
import React, { useEffect, useMemo, useState } from 'react';
import { ActivityIndicator, Platform, Pressable, StyleSheet, Text, useWindowDimensions, View } from 'react-native';

import { CatalogFiltersSidebar } from '../../components/CatalogFiltersSidebar';
import { MedicineCard } from '../../components/MedicineCard';
import { OfferRow } from '../../components/OfferRow';
import { Screen } from '../../components/Screen';
import { SearchBar } from '../../components/SearchBar';
import { useCatalogContext } from '../../context/AppContext';
import { CatalogSort, getLaboratoryDisplayName } from '../../services/catalog';
import { palette, radius, spacing } from '../../theme';
import { Medicamento } from '../../types/api';

type SearchRow = { medicine: Medicamento; medicineId: number; medicineName: string; pharmacyId: number; pharmacyName: string; price: number };

export default function SearchScreen() {
  const router = useRouter();
  const params = useLocalSearchParams<{ q?: string }>();
  const { loadCatalogPage, cheapestMedicines, cheapestError, selectedFilters, selectedPharmacyId, markAsViewed } = useCatalogContext();
  const { width } = useWindowDimensions();
  const [search, setSearch] = useState(typeof params.q === 'string' ? params.q : '');
  const [query, setQuery] = useState(typeof params.q === 'string' ? params.q : '');
  const [items, setItems] = useState<Medicamento[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [reload, setReload] = useState(0);
  const [sortBy, setSortBy] = useState<CatalogSort>('menor_preco');
  const [sortMenuVisible, setSortMenuVisible] = useState(false);
  const hasDesktopSidebar = Platform.OS === 'web' && width >= 1120;
  const desktopColumns = hasDesktopSidebar ? (width >= 1640 ? 4 : width >= 1280 ? 3 : 2) : undefined;
  const limit = 20;
  const totalPages = Math.max(1, Math.ceil(total / limit));

  useEffect(() => {
    if (typeof params.q === 'string') {
      setSearch(params.q);
      setQuery(params.q);
    }
  }, [params.q]);

  useEffect(() => {
    const timer = setTimeout(() => setQuery(search.trim()), 300);
    return () => clearTimeout(timer);
  }, [search]);

  useEffect(() => { setPage(1); }, [query, selectedFilters, selectedPharmacyId, sortBy]);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(false);
    loadCatalogPage({ query, pharmacyId: selectedPharmacyId ?? undefined, offset: (page - 1) * limit, limit, filters: selectedFilters, sortBy })
      .then((result) => {
        if (!active) return;
        setItems(result.items);
        setTotal(result.total);
      })
      .catch(() => { if (active) setError(true); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [loadCatalogPage, query, page, selectedFilters, selectedPharmacyId, sortBy, reload]);

  const rows = useMemo<SearchRow[]>(() => items
    .flatMap((medicine) => medicine.ofertas
      .filter((offer) => offer.disponivel)
      .map((offer) => ({
        medicine,
        medicineId: medicine.id,
        medicineName: medicine.nome,
        pharmacyId: offer.farmacia_id,
        pharmacyName: offer.farmacia_nome || offer.farmacia?.nome_fantasia || `Farmácia ${offer.farmacia_id}`,
        price: Number(offer.preco),
      })))
    .sort((a, b) => {
      if (sortBy === 'relevancia') return 0;
      if (sortBy === 'maior_preco') return b.price - a.price;
      if (sortBy === 'nome') return a.medicineName.localeCompare(b.medicineName, 'pt-BR');
      return a.price - b.price;
    }), [items, sortBy]);

  const sortOptions: { value: CatalogSort; label: string }[] = [
    { value: 'menor_preco', label: 'Menor preço' },
    { value: 'maior_preco', label: 'Maior preço' },
    { value: 'nome', label: 'Nome (A–Z)' },
    { value: 'relevancia', label: 'Relevância' },
  ];
  const selectedSortLabel = sortOptions.find((option) => option.value === sortBy)?.label ?? 'Menor preço';
  const showCheapestSuggestions = !query && selectedFilters.length === 0 && selectedPharmacyId === null;

  const openDetail = (id: number) => {
    markAsViewed(id);
    router.push(`/medicine/${id}`);
  };

  return (
    <Screen contentStyle={styles.screen} wideContent={hasDesktopSidebar}>
      <Text style={styles.eyebrow}>BUSCA DE MEDICAMENTOS</Text>
      <Text style={styles.title}>Compare ofertas</Text>
      <SearchBar
        value={search}
        onChangeText={setSearch}
        onSubmit={() => { setQuery(search.trim()); router.setParams({ q: search.trim() }); }}
        onFilter={hasDesktopSidebar ? undefined : () => router.push('/filters')}
        filterCount={selectedFilters.length + Number(selectedPharmacyId !== null)}
      />
      <View style={[styles.catalogLayout, hasDesktopSidebar && styles.catalogLayoutDesktop]}>
      {hasDesktopSidebar && <CatalogFiltersSidebar />}
      <View style={[styles.catalogResults, hasDesktopSidebar && styles.catalogResultsDesktop]}>
      <View style={styles.resultHeader}>
        <Text style={styles.resultCount}>{total.toLocaleString('pt-BR')} resultados</Text>
        <View style={styles.resultActions}>
          {hasDesktopSidebar && <View style={styles.sortWrap}>
            <Pressable onPress={() => setSortMenuVisible((visible) => !visible)} accessibilityRole="button" accessibilityLabel={`Ordenar: ${selectedSortLabel}`} accessibilityState={{ expanded: sortMenuVisible }} style={styles.sortButton}>
              <Ionicons name="swap-vertical-outline" size={16} color={palette.primaryDark} />
              <Text style={styles.sortButtonText}>{selectedSortLabel}</Text>
              <Ionicons name={sortMenuVisible ? 'chevron-up' : 'chevron-down'} size={14} color={palette.textSoft} />
            </Pressable>
            {sortMenuVisible && <View style={styles.sortMenu}>
              {sortOptions.map((option) => <Pressable key={option.value} onPress={() => { setSortBy(option.value); setPage(1); setSortMenuVisible(false); }} accessibilityRole="button" accessibilityState={{ selected: sortBy === option.value }} style={[styles.sortOption, sortBy === option.value && styles.sortOptionActive]}>
                <Text style={[styles.sortOptionText, sortBy === option.value && styles.sortOptionTextActive]}>{option.label}</Text>
                {sortBy === option.value && <Ionicons name="checkmark" size={16} color={palette.primaryDark} />}
              </Pressable>)}
            </View>}
          </View>}
          {loading && <ActivityIndicator size="small" color={palette.primary} />}
        </View>
      </View>
      {error ? (
        <View style={styles.messageBox}>
          <Text style={styles.message}>Não foi possível realizar a busca.</Text>
          <Pressable onPress={() => setReload((value) => value + 1)} accessibilityRole="button"><Text style={styles.retry}>Tentar novamente</Text></Pressable>
        </View>
      ) : !loading && rows.length === 0 ? (
        <View style={styles.messageBox}><Text style={styles.message}>Nenhuma oferta corresponde à busca e aos filtros.</Text></View>
      ) : (
        <View style={[styles.rows, hasDesktopSidebar && styles.rowsDesktop]}>
          {rows.map((row) => (
            <View key={`${row.medicineId}-${row.pharmacyId}`} style={hasDesktopSidebar && (width >= 1640 ? styles.offerGridItemThree : styles.offerGridItemTwo)}>
              <OfferRow medicine={row.medicine} medicineName={row.medicineName} laboratory={getLaboratoryDisplayName(row.medicine.laboratorio)} pharmacy={row.pharmacyName} price={row.price} color={palette.primary} onPress={() => openDetail(row.medicineId)} />
            </View>
          ))}
        </View>
      )}
      {totalPages > 1 && (
        <View style={styles.pagination}>
          <Pressable disabled={page <= 1 || loading} onPress={() => setPage((value) => Math.max(1, value - 1))} accessibilityRole="button"><Text style={styles.pageAction}>Anterior</Text></Pressable>
          <Text style={styles.pageInfo}>Página {page} de {totalPages}</Text>
          <Pressable disabled={page >= totalPages || loading} onPress={() => setPage((value) => Math.min(totalPages, value + 1))} accessibilityRole="button"><Text style={styles.pageAction}>Próxima</Text></Pressable>
        </View>
      )}
      {showCheapestSuggestions && <View style={styles.bottomSection}>
        <Text style={styles.sectionTitle}>Menores preços disponíveis</Text>
        {cheapestError ? <Text style={styles.message}>Não foi possível carregar os menores preços.</Text> : (
          <View style={[styles.grid, width <= 480 && styles.gridMobileList]}>
            {cheapestMedicines.map((medicine) => <MedicineCard key={medicine.id} medicine={medicine} onPress={() => openDetail(medicine.id)} compact columns={desktopColumns} />)}
          </View>
        )}
      </View>}
      </View>
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  screen: { paddingTop: spacing.lg },
  catalogLayout: { width: '100%' },
  catalogLayoutDesktop: { flexDirection: 'row', alignItems: 'flex-start', gap: spacing.lg },
  catalogResults: { width: '100%', minWidth: 0, flex: 1 },
  catalogResultsDesktop: { width: 'auto', flex: 1 },
  eyebrow: { color: palette.primaryDark, fontSize: 11, fontWeight: '800', letterSpacing: 1 },
  title: { color: palette.text, fontSize: 25, fontWeight: '800', marginVertical: spacing.sm },
  resultHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: spacing.md, zIndex: 2 },
  resultActions: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  sortWrap: { position: 'relative', zIndex: 3 },
  sortButton: { minHeight: 38, flexDirection: 'row', alignItems: 'center', gap: 7, paddingHorizontal: spacing.sm, borderWidth: 1, borderColor: palette.border, borderRadius: radius.sm, backgroundColor: palette.surface },
  sortButtonText: { color: palette.text, fontSize: 13, fontWeight: '600' },
  sortMenu: { position: 'absolute', top: 43, right: 0, minWidth: 174, padding: 4, borderWidth: 1, borderColor: palette.border, borderRadius: radius.sm, backgroundColor: palette.surface, shadowColor: '#172033', shadowOpacity: 0.12, shadowRadius: 12, shadowOffset: { width: 0, height: 6 }, elevation: 5, zIndex: 4 },
  sortOption: { minHeight: 38, paddingHorizontal: spacing.sm, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', borderRadius: radius.sm },
  sortOptionActive: { backgroundColor: palette.primarySoft },
  sortOptionText: { color: palette.text, fontSize: 13 },
  sortOptionTextActive: { color: palette.primaryDark, fontWeight: '700' },
  resultCount: { color: palette.textSoft, fontSize: 13 },
  rows: { marginTop: spacing.sm },
  rowsDesktop: { flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'space-between', columnGap: spacing.md },
  offerGridItemTwo: { width: '48.5%', minWidth: 0 },
  offerGridItemThree: { width: '32%', minWidth: 0 },
  messageBox: { marginTop: spacing.md, padding: spacing.lg, borderRadius: radius.md, backgroundColor: palette.surface },
  message: { color: palette.textSoft, fontSize: 14 },
  retry: { color: palette.primaryDark, fontWeight: '700', marginTop: spacing.sm },
  pagination: { flexDirection: 'row', justifyContent: 'center', alignItems: 'center', gap: spacing.lg, marginTop: spacing.md },
  pageAction: { color: palette.primaryDark, fontWeight: '700', padding: spacing.sm },
  pageInfo: { color: palette.textSoft, fontSize: 13 },
  bottomSection: { marginTop: spacing.xl },
  sectionTitle: { color: palette.text, fontSize: 19, fontWeight: '700', marginBottom: spacing.md },
  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.md },
  gridMobileList: { flexDirection: 'column', flexWrap: 'nowrap' },
});
