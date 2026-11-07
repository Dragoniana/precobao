import Ionicons from '@expo/vector-icons/Ionicons';
import { useRouter } from 'expo-router';
import React, { useEffect, useMemo, useState } from 'react';
import { ActivityIndicator, Platform, Pressable, Text, useWindowDimensions, View } from 'react-native';

import { FilterChip } from '../../components/FilterChip';
import { CatalogFiltersSidebar } from '../../components/CatalogFiltersSidebar';
import { MedicineCard } from '../../components/MedicineCard';
import { Screen } from '../../components/Screen';
import { markMedicineOpenTrace, startMedicineOpenTrace } from '../../services/medicineOpenPerformance';
import { SearchBar } from '../../components/SearchBar';
import { useCatalogContext } from '../../context/AppContext';
import { api } from '../../services/api';
import { CatalogSort } from '../../services/catalog';
import { Medicamento } from '../../types/api';
import { radius, spacing } from '../../theme';
import { createThemedStyles, useAppTheme } from '../../context/ThemeContext';

type Pharmacy = { id: number; nome_fantasia: string };

export default function HomeScreen() {
  const styles = useStyles();
  const { palette } = useAppTheme();
  const router = useRouter();
  const { width } = useWindowDimensions();
  const { markAsViewed, selectedFilters, selectedPharmacyId, setSelectedPharmacyId, loadCatalogPage } = useCatalogContext();
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [pharmacies, setPharmacies] = useState<Pharmacy[]>([]);
  const [pharmaciesLoading, setPharmaciesLoading] = useState(true);
  const [pharmaciesError, setPharmaciesError] = useState(false);
  const [items, setItems] = useState<Medicamento[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [reload, setReload] = useState(0);
  const [sortBy, setSortBy] = useState<CatalogSort>('relevancia');
  const [sortMenuVisible, setSortMenuVisible] = useState(false);
  const limit = width < 520 ? 8 : 12;
  const totalPages = Math.max(1, Math.ceil(total / limit));
  const isWeb = Platform.OS === 'web';
  const hasDesktopSidebar = isWeb && width >= 1120;
  const desktopColumns = hasDesktopSidebar ? (width >= 1640 ? 4 : width >= 1280 ? 3 : 2) : undefined;

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(search.trim()), 280);
    return () => clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    let active = true;
    api.get<Pharmacy[]>('/catalogo/farmacias')
      .then(({ data }) => { if (active) setPharmacies(data); })
      .catch(() => { if (active) setPharmaciesError(true); })
      .finally(() => { if (active) setPharmaciesLoading(false); });
    return () => { active = false; };
  }, []);

  useEffect(() => { setPage(1); }, [debouncedSearch, selectedPharmacyId, selectedFilters, sortBy]);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(false);
    loadCatalogPage({
      query: debouncedSearch,
      pharmacyId: selectedPharmacyId ?? undefined,
      offset: (page - 1) * limit,
      limit,
      filters: selectedFilters,
      sortBy,
    }).then((result) => {
      if (!active) return;
      setItems(result.items);
      setTotal(result.total);
    }).catch(() => {
      if (active) setError(true);
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, [debouncedSearch, selectedPharmacyId, selectedFilters, sortBy, page, limit, loadCatalogPage, reload]);

  const sortOptions: { value: CatalogSort; label: string }[] = [
    { value: 'relevancia', label: 'Relevância' },
    { value: 'menor_preco', label: 'Menor preço' },
    { value: 'maior_preco', label: 'Maior preço' },
    { value: 'nome', label: 'Nome (A–Z)' },
  ];
  const selectedSortLabel = sortOptions.find((option) => option.value === sortBy)?.label ?? 'Relevância';

  const heading = useMemo(() => selectedPharmacyId === null
    ? 'Compare preços de medicamentos'
    : pharmacies.find((item) => item.id === selectedPharmacyId)?.nome_fantasia || 'Medicamentos', [pharmacies, selectedPharmacyId]);

  const openDetail = (id: number) => {
    startMedicineOpenTrace(id, 'home');
    markAsViewed(id);
    markMedicineOpenTrace(id, 'navigation-start');
    router.push(`/medicine/${id}`);
  };

  return (
    <Screen contentStyle={styles.screen} wideContent={hasDesktopSidebar}>
      <View style={[styles.intro, width <= 480 && styles.introCompact]}>
        <Text style={styles.eyebrow}>PREÇO BÃO · COMPARADOR</Text>
        <Text style={[styles.pageTitle, width <= 480 && styles.pageTitleCompact]}>Encontre o melhor preço</Text>
        <Text style={styles.description}>{'Pesquise e compare pre\u00e7os de medicamentos.'}</Text>
      </View>

      <SearchBar
        value={search}
        onChangeText={setSearch}
        onSubmit={() => setDebouncedSearch(search.trim())}
        onFilter={hasDesktopSidebar ? undefined : () => router.push('/filters')}
        filterCount={selectedFilters.length + Number(selectedPharmacyId !== null)}
        placeholder={width <= 360 ? 'Medicamento ou princípio ativo' : 'Buscar medicamento ou princípio ativo'}
      />

      <View style={[styles.catalogLayout, hasDesktopSidebar && styles.catalogLayoutDesktop]}>
      {hasDesktopSidebar && <CatalogFiltersSidebar />}
      <View style={[styles.catalogResults, hasDesktopSidebar && styles.catalogResultsDesktop]}>
      {!hasDesktopSidebar && <>
        <View style={[styles.filterHeader, width <= 480 && styles.filterHeaderCompact]}>
          <Text style={styles.sectionLabel}>Farmácias</Text>
        </View>
        {pharmaciesLoading ? <ActivityIndicator color={palette.primary} /> : pharmaciesError ? (
          <Text style={styles.message}>Não foi possível carregar as farmácias.</Text>
        ) : (
          <View style={styles.pharmacyFilters}>
            <FilterChip label="Todas" active={selectedPharmacyId === null} onPress={() => setSelectedPharmacyId(null)} />
            {pharmacies.map((pharmacy) => (
              <FilterChip
                key={pharmacy.id}
                label={pharmacy.nome_fantasia}
                active={selectedPharmacyId === pharmacy.id}
                onPress={() => setSelectedPharmacyId(pharmacy.id)}
              />
            ))}
          </View>
        )}
      </>}

      <View style={[styles.resultHeader, width < 520 && styles.resultHeaderCompact]}>
        <View>
          <Text style={styles.sectionTitle}>{heading}</Text>
          <Text style={styles.resultCount}>{total.toLocaleString('pt-BR')} medicamentos</Text>
        </View>
        <View style={[styles.resultActions, width < 520 && styles.resultActionsCompact]}>
          <View style={styles.sortWrap}>
            <Pressable
              onPress={() => setSortMenuVisible((visible) => !visible)}
              accessibilityRole="button"
              accessibilityLabel={`Ordenar: ${selectedSortLabel}`}
              accessibilityState={{ expanded: sortMenuVisible }}
              style={styles.sortButton}
            >
              <Ionicons name="swap-vertical-outline" size={16} color={palette.primaryDark} />
              <Text style={styles.sortButtonText}>{selectedSortLabel}</Text>
              <Ionicons name={sortMenuVisible ? 'chevron-up' : 'chevron-down'} size={14} color={palette.textSoft} />
            </Pressable>
            {sortMenuVisible && (
              <View style={styles.sortMenu}>
                {sortOptions.map((option) => (
                  <Pressable
                    key={option.value}
                    onPress={() => { setSortBy(option.value); setSortMenuVisible(false); }}
                    accessibilityRole="button"
                    accessibilityState={{ selected: sortBy === option.value }}
                    style={[styles.sortOption, sortBy === option.value && styles.sortOptionActive]}
                  >
                    <Text style={[styles.sortOptionText, sortBy === option.value && styles.sortOptionTextActive]}>{option.label}</Text>
                    {sortBy === option.value && <Ionicons name="checkmark" size={16} color={palette.primaryDark} />}
                  </Pressable>
                ))}
              </View>
            )}
          </View>
          {loading && <ActivityIndicator size="small" color={palette.primary} />}
        </View>
      </View>

      {error ? (
        <View style={styles.messageBox}>
          <Text style={styles.message}>Não foi possível carregar os medicamentos.</Text>
          <Pressable onPress={() => setReload((current) => current + 1)} accessibilityRole="button">
            <Text style={styles.retry}>Tentar novamente</Text>
          </Pressable>
        </View>
      ) : loading && items.length === 0 ? (
        <View style={styles.loading}><ActivityIndicator size="large" color={palette.primary} /><Text style={styles.message}>Buscando ofertas...</Text></View>
      ) : items.length === 0 ? (
        <View style={styles.messageBox}>
          <Text style={styles.sectionTitle}>Nenhum medicamento encontrado</Text>
          <Text style={styles.message}>Altere a busca ou remova algum filtro para ver mais ofertas.</Text>
        </View>
      ) : (
        <View style={[styles.grid, isWeb && styles.gridWeb, width <= 480 && styles.gridMobileList]}>
          {items.map((medicine) => (
            <MedicineCard key={medicine.id} medicine={medicine} onPress={() => openDetail(medicine.id)} compact columns={desktopColumns} />
          ))}
        </View>
      )}

      {totalPages > 1 && (
        <View style={styles.pagination}>
          <Pressable disabled={page <= 1 || loading} onPress={() => setPage((current) => Math.max(1, current - 1))} accessibilityRole="button" style={styles.pageButton}>
            <Text style={styles.pageButtonText}>Anterior</Text>
          </Pressable>
          <Text style={styles.pageInfo}>Página {page} de {totalPages}</Text>
          <Pressable disabled={page >= totalPages || loading} onPress={() => setPage((current) => Math.min(totalPages, current + 1))} accessibilityRole="button" style={styles.pageButton}>
            <Text style={styles.pageButtonText}>Próxima</Text>
          </Pressable>
        </View>
      )}
      </View>
      </View>
    </Screen>
  );
}

const useStyles = createThemedStyles((palette) => ({
  screen: { paddingTop: spacing.xs, paddingBottom: spacing.xl },
  catalogLayout: { width: '100%' },
  catalogLayoutDesktop: { marginTop: 22, flexDirection: 'row', alignItems: 'flex-start', gap: spacing.lg },
  catalogResults: { width: '100%', minWidth: 0, flex: 1 },
  catalogResultsDesktop: { width: 'auto', flex: 1 },
  intro: { marginBottom: spacing.lg, paddingBottom: spacing.md, borderBottomWidth: 1, borderBottomColor: palette.border },
  introCompact: { marginBottom: spacing.sm, paddingBottom: spacing.xs },
  eyebrow: { color: palette.primaryDark, fontSize: 10, fontWeight: '800', letterSpacing: 1.1 },
  pageTitle: { color: palette.text, fontSize: 32, lineHeight: 39, fontWeight: '800', letterSpacing: -0.5, marginTop: spacing.xs },
  pageTitleCompact: { fontSize: 25, lineHeight: 31 },
  description: { color: palette.textSoft, fontSize: 14, lineHeight: 20, marginTop: spacing.xs },
  filterHeader: { flexDirection: 'row', alignItems: 'center', marginTop: spacing.md, paddingBottom: spacing.xs, borderBottomWidth: 1, borderBottomColor: palette.border },
  filterHeaderCompact: { marginTop: spacing.sm, paddingBottom: 2 },
  sectionLabel: { color: palette.text, fontSize: 14, fontWeight: '700' },
  pharmacyFilters: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.xs, marginTop: spacing.sm },
  resultHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: spacing.xl, marginBottom: spacing.md, paddingBottom: spacing.sm, borderBottomWidth: 1, borderBottomColor: palette.border, zIndex: 2 },
  resultHeaderCompact: { flexDirection: 'column', alignItems: 'flex-start', gap: spacing.sm, marginTop: spacing.md },
  resultActions: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  resultActionsCompact: { width: '100%', justifyContent: 'space-between' },
  sortWrap: { position: 'relative', zIndex: 3 },
  sortButton: { minHeight: 38, flexDirection: 'row', alignItems: 'center', gap: 7, paddingHorizontal: spacing.sm, borderWidth: 1, borderColor: palette.border, borderRadius: radius.sm, backgroundColor: palette.surface },
  sortButtonText: { color: palette.text, fontSize: 13, fontWeight: '600' },
  sortMenu: { position: 'absolute', top: 43, right: 0, minWidth: 174, padding: 4, borderWidth: 1, borderColor: palette.border, borderRadius: radius.sm, backgroundColor: palette.surface, shadowColor: palette.shadow, shadowOpacity: 0.12, shadowRadius: 12, shadowOffset: { width: 0, height: 6 }, elevation: 5, zIndex: 4 },
  sortOption: { minHeight: 38, paddingHorizontal: spacing.sm, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', borderRadius: radius.sm },
  sortOptionActive: { backgroundColor: palette.primarySoft },
  sortOptionText: { color: palette.text, fontSize: 13 },
  sortOptionTextActive: { color: palette.primaryDark, fontWeight: '700' },
  sectionTitle: { color: palette.text, fontSize: 19, fontWeight: '800' },
  resultCount: { color: palette.textSoft, fontSize: 13, marginTop: 2 },
  grid: { flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'space-between', rowGap: spacing.md },
  gridWeb: { justifyContent: 'space-between', columnGap: spacing.md, rowGap: spacing.lg },
  gridMobileList: { width: '100%', flexDirection: 'column', flexWrap: 'nowrap', alignItems: 'stretch', justifyContent: 'flex-start', rowGap: spacing.sm },
  messageBox: { padding: spacing.lg, backgroundColor: palette.surface, borderRadius: radius.md, gap: spacing.sm },
  message: { color: palette.textSoft, fontSize: 14, lineHeight: 20 },
  retry: { color: palette.primaryDark, fontSize: 14, fontWeight: '700' },
  loading: { minHeight: 180, alignItems: 'center', justifyContent: 'center', gap: spacing.sm },
  pagination: { marginTop: spacing.xl, paddingTop: spacing.md, borderTopWidth: 1, borderTopColor: palette.border, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: spacing.md },
  pageButton: { minHeight: 40, justifyContent: 'center', backgroundColor: palette.primary, paddingHorizontal: spacing.lg, paddingVertical: spacing.sm, borderRadius: radius.sm },
  pageButtonText: { color: palette.onPrimary, fontSize: 13, fontWeight: '700' },
  pageInfo: { color: palette.text, fontSize: 13, fontWeight: '600' },
}));
