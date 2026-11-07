import Ionicons from '@expo/vector-icons/Ionicons';
import React, { useEffect, useMemo, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  Modal,
  Pressable,
  ScrollView,
  Text,
  TextInput,
  View,
} from 'react-native';

import { FilterChip } from './FilterChip';
import { FilterOption, useCatalogContext } from '../context/AppContext';
import { api } from '../services/api';
import { formatLaboratoryLabel, normalizeText } from '../services/catalog';
import { radius, spacing } from '../theme';
import { createThemedStyles, useAppTheme } from '../context/ThemeContext';

type Pharmacy = { id: number; nome_fantasia: string };
type Section = 'categoria' | 'tipo_venda';

const isVisibleFilter = (filter: FilterOption) =>
  !['principio_ativo', 'referencia', 'classificacao'].includes(String(filter.type));

const labels: Record<Section, string> = {
  categoria: 'Tipo de medicamento',
  tipo_venda: 'Tipo de venda',
};

export function CatalogFiltersSidebar() {
  const styles = useStyles();
  const { palette } = useAppTheme();
  const {
    selectedFilters,
    filterOptions,
    applyFilters,
    clearFilters,
    selectedPharmacyId,
    setSelectedPharmacyId,
  } = useCatalogContext();
  const [draftFilters, setDraftFilters] = useState(() => selectedFilters.filter(isVisibleFilter));
  const [draftPharmacyId, setDraftPharmacyId] = useState(selectedPharmacyId);
  const [expanded, setExpanded] = useState<Section[]>(['tipo_venda']);
  const [pharmacyExpanded, setPharmacyExpanded] = useState(false);
  const [search, setSearch] = useState('');
  const [labSearch, setLabSearch] = useState('');
  const [laboratoriesOpen, setLaboratoriesOpen] = useState(false);
  const [pharmacies, setPharmacies] = useState<Pharmacy[]>([]);
  const [pharmaciesLoading, setPharmaciesLoading] = useState(true);

  useEffect(() => {
    setDraftFilters(selectedFilters.filter(isVisibleFilter));
    setDraftPharmacyId(selectedPharmacyId);
  }, [selectedFilters, selectedPharmacyId]);

  useEffect(() => {
    let active = true;
    api.get<Pharmacy[]>('/catalogo/farmacias')
      .then(({ data }) => { if (active) setPharmacies(data); })
      .catch(() => { if (active) setPharmacies([]); })
      .finally(() => { if (active) setPharmaciesLoading(false); });
    return () => { active = false; };
  }, []);

  const groupedOptions = useMemo(() => {
    const needle = normalizeText(search);
    const visible = filterOptions.filter((option) => !needle || normalizeText(option.label).includes(needle));
    return {
      categoria: visible.filter((option) => option.type === 'categoria' && ['similar', 'generico'].includes(normalizeText(option.value))),
      tipo_venda: visible.filter((option) => option.type === 'receita' || option.type === 'controle_especial'),
    } satisfies Record<Section, FilterOption[]>;
  }, [filterOptions, search]);

  const laboratories = filterOptions.filter((option) => option.type === 'laboratorio');
  const selectedLaboratories = draftFilters.filter((option) => option.type === 'laboratorio');
  const visibleLaboratories = useMemo(() => {
    const needle = normalizeText(labSearch);
    return laboratories.filter((option) => !needle || normalizeText(option.label).includes(needle));
  }, [laboratories, labSearch]);
  const selectedPharmacy = pharmacies.find((pharmacy) => pharmacy.id === draftPharmacyId);
  const appliedIds = selectedFilters.filter(isVisibleFilter).map((option) => option.id).sort().join('|');
  const draftIds = draftFilters.map((option) => option.id).sort().join('|');
  const hasChanges = appliedIds !== draftIds || selectedPharmacyId !== draftPharmacyId;
  const selectionCount = draftFilters.length + Number(draftPharmacyId !== null);

  const toggleFilter = (option: FilterOption) => setDraftFilters((current) => current.some((item) => item.id === option.id)
    ? current.filter((item) => item.id !== option.id)
    : [...current, option]);
  const toggleSection = (section: Section) => setExpanded((current) => current.includes(section)
    ? current.filter((item) => item !== section)
    : [...current, section]);
  const apply = () => {
    applyFilters(draftFilters.filter(isVisibleFilter));
    setSelectedPharmacyId(draftPharmacyId);
  };
  const clear = () => {
    setDraftFilters([]);
    setDraftPharmacyId(null);
    setSearch('');
    setLabSearch('');
    clearFilters();
    setSelectedPharmacyId(null);
  };

  return (
    <View style={styles.shell}>
      <View style={styles.panelHeader}>
        <View style={styles.headingCopy}>
          <Text style={styles.title}>Filtros</Text>
          <Text style={styles.subtitle}>Refine os resultados</Text>
        </View>
        <Text style={[styles.status, hasChanges && styles.statusPending]}>{hasChanges ? 'Pendentes' : 'Aplicados'}</Text>
      </View>

      {selectionCount > 0 && (
        <View style={styles.appliedArea}>
          <View style={styles.appliedHeading}>
            <Text style={styles.appliedLabel}>Ativos</Text>
            <Text style={styles.count}>{selectionCount}</Text>
          </View>
          <View style={styles.activeChips}>
            {draftFilters.map((option) => <FilterChip key={option.id} label={option.label} active compact onPress={() => toggleFilter(option)} accessibilityLabel={`Remover ${option.label}`} />)}
            {selectedPharmacy && <FilterChip label={selectedPharmacy.nome_fantasia} active compact onPress={() => setDraftPharmacyId(null)} accessibilityLabel={`Remover farmácia ${selectedPharmacy.nome_fantasia}`} />}
          </View>
        </View>
      )}

      <View style={styles.searchBox}>
        <Ionicons name="search-outline" size={16} color={palette.textSoft} />
        <TextInput
          accessibilityLabel="Buscar opção de filtro"
          value={search}
          onChangeText={setSearch}
          placeholder="Buscar filtros"
          placeholderTextColor={palette.muted}
          autoCapitalize="none"
          autoCorrect={false}
          style={styles.searchInput}
        />
        {!!search && <Pressable accessibilityRole="button" accessibilityLabel="Limpar busca de filtros" onPress={() => setSearch('')}><Ionicons name="close-circle" size={17} color={palette.textSoft} /></Pressable>}
      </View>

      <ScrollView style={styles.panelScroll} contentContainerStyle={styles.panelContent} showsVerticalScrollIndicator>
        {(['categoria', 'tipo_venda'] as const).map((section) => {
          const options = groupedOptions[section];
          if (!options.length) return null;
          const selected = section === 'tipo_venda'
            ? draftFilters.filter((option) => option.type === 'receita' || option.type === 'controle_especial')
            : draftFilters.filter((option) => option.type === 'categoria');
          const isExpanded = expanded.includes(section) || Boolean(search.trim());
          return (
            <View key={section} style={styles.section}>
              <Pressable accessibilityRole="button" accessibilityState={{ expanded: isExpanded }} onPress={() => toggleSection(section)} style={styles.sectionHeader}>
                <Text style={styles.sectionTitle}>{labels[section]}</Text>
                {!!selected.length && <Text style={styles.count}>{selected.length}</Text>}
                <Ionicons name={isExpanded ? 'chevron-up' : 'chevron-down'} size={16} color={palette.textSoft} />
              </Pressable>
              {isExpanded && <View style={styles.options}>
                {options.map((option) => <FilterChip key={option.id} label={option.label} active={draftFilters.some((item) => item.id === option.id)} compact onPress={() => toggleFilter(option)} />)}
              </View>}
            </View>
          );
        })}

        <View style={styles.section}>
          <View style={styles.sectionHeader}>
            <View style={styles.sectionName}>
              <Text style={styles.sectionTitle}>Laboratórios</Text>
              <Text numberOfLines={1} style={styles.sectionSummary}>{selectedLaboratories.length ? `${selectedLaboratories.length} selecionado(s)` : 'Todos'}</Text>
            </View>
            {!!selectedLaboratories.length && <Text style={styles.count}>{selectedLaboratories.length}</Text>}
            <Pressable accessibilityRole="button" accessibilityLabel="Selecionar laboratórios" onPress={() => setLaboratoriesOpen(true)} style={styles.selectButton}>
              <Text style={styles.selectText}>Selecionar</Text>
            </Pressable>
          </View>
        </View>

        {pharmaciesLoading ? (
          <View style={styles.pharmacyLoading}><ActivityIndicator size="small" color={palette.primary} /><Text style={styles.sectionSummary}>Carregando farmácias</Text></View>
        ) : pharmacies.length > 0 && (
          <View style={styles.section}>
            <Pressable accessibilityRole="button" accessibilityState={{ expanded: pharmacyExpanded }} onPress={() => setPharmacyExpanded((current) => !current)} style={styles.sectionHeader}>
              <View style={styles.sectionName}>
                <Text style={styles.sectionTitle}>Farmácia</Text>
                <Text numberOfLines={1} style={styles.sectionSummary}>{selectedPharmacy?.nome_fantasia ?? 'Todas'}</Text>
              </View>
              <Ionicons name={pharmacyExpanded ? 'chevron-up' : 'chevron-down'} size={16} color={palette.textSoft} />
            </Pressable>
            {pharmacyExpanded && <View style={styles.options}>
              <FilterChip label="Todas" active={draftPharmacyId === null} compact onPress={() => setDraftPharmacyId(null)} />
              {pharmacies.map((pharmacy) => <FilterChip key={pharmacy.id} label={pharmacy.nome_fantasia} active={draftPharmacyId === pharmacy.id} compact onPress={() => setDraftPharmacyId(pharmacy.id)} />)}
            </View>}
          </View>
        )}
      </ScrollView>

      <View style={styles.actions}>
        <Pressable accessibilityRole="button" onPress={clear} style={({ pressed }) => [styles.clearButton, pressed && styles.pressed]}>
          <Text style={styles.clearText}>Limpar</Text>
        </Pressable>
        <Pressable accessibilityRole="button" onPress={apply} style={({ pressed }) => [styles.applyButton, pressed && styles.pressed]}>
          <Text style={styles.applyText}>Aplicar filtros</Text>
        </Pressable>
      </View>

      <Modal visible={laboratoriesOpen} transparent animationType="fade" onRequestClose={() => setLaboratoriesOpen(false)}>
        <View style={styles.modalOverlay}>
          <View style={styles.modal}>
            <View style={styles.modalHeader}>
              <View style={styles.headingCopy}><Text style={styles.modalTitle}>Laboratórios</Text><Text style={styles.sectionSummary}>{selectedLaboratories.length} de {laboratories.length} selecionado(s)</Text></View>
              <Pressable accessibilityRole="button" accessibilityLabel="Fechar seletor de laboratórios" onPress={() => setLaboratoriesOpen(false)} style={styles.closeButton}><Ionicons name="close" size={19} color={palette.text} /></Pressable>
            </View>
            <View style={styles.labSearch}>
              <Ionicons name="search-outline" size={17} color={palette.textSoft} />
              <TextInput accessibilityLabel="Buscar laboratório" value={labSearch} onChangeText={setLabSearch} placeholder="Buscar laboratório" placeholderTextColor={palette.muted} autoCapitalize="none" autoCorrect={false} style={styles.searchInput} />
              {!!labSearch && <Pressable accessibilityRole="button" accessibilityLabel="Limpar busca por laboratório" onPress={() => setLabSearch('')}><Ionicons name="close-circle" size={17} color={palette.textSoft} /></Pressable>}
            </View>
            <FlatList
              data={visibleLaboratories}
              keyExtractor={(option) => option.id}
              keyboardShouldPersistTaps="handled"
              style={styles.labList}
              ListEmptyComponent={<Text style={styles.emptyText}>Nenhum laboratório encontrado.</Text>}
              renderItem={({ item }) => {
                const active = draftFilters.some((option) => option.id === item.id);
                return <Pressable accessibilityRole="checkbox" accessibilityState={{ checked: active }} onPress={() => toggleFilter(item)} style={[styles.labOption, active && styles.labOptionSelected]}>
                  <View style={[styles.checkbox, active && styles.checkboxActive]}>{active && <Ionicons name="checkmark" size={14} color={palette.surface} />}</View>
                  <Text numberOfLines={2} style={[styles.labName, active && styles.labNameActive]}>{formatLaboratoryLabel(item.value)}</Text>
                </Pressable>;
              }}
            />
            <View style={styles.modalFooter}>
              <Pressable accessibilityRole="button" onPress={() => setDraftFilters((current) => current.filter((item) => item.type !== 'laboratorio'))} style={styles.modalClear}><Text style={styles.clearText}>Limpar seleção</Text></Pressable>
              <Pressable accessibilityRole="button" onPress={() => setLaboratoriesOpen(false)} style={styles.modalDone}><Text style={styles.applyText}>Concluir</Text></Pressable>
            </View>
          </View>
        </View>
      </Modal>
    </View>
  );
}

const useStyles = createThemedStyles((palette) => ({
  shell: {
    width: 276,
    flexShrink: 0,
    maxHeight: 'calc(100vh - 112px)' as any,
    minHeight: 500,
    position: 'sticky' as any,
    top: spacing.md,
    borderWidth: 1,
    borderColor: palette.border,
    borderRadius: radius.lg,
    backgroundColor: palette.surface,
    overflow: 'hidden',
  },
  panelHeader: { minHeight: 68, flexDirection: 'row', alignItems: 'center', gap: spacing.sm, paddingHorizontal: spacing.md, borderBottomWidth: 1, borderBottomColor: palette.border },
  headingCopy: { flex: 1, minWidth: 0 },
  title: { color: palette.text, fontSize: 18, lineHeight: 23, fontWeight: '800' },
  subtitle: { marginTop: 2, color: palette.textSoft, fontSize: 12 },
  status: { color: palette.textSoft, fontSize: 10, fontWeight: '700' },
  statusPending: { color: palette.primaryDark },
  appliedArea: { paddingHorizontal: spacing.md, paddingTop: spacing.sm, paddingBottom: spacing.sm, borderBottomWidth: 1, borderBottomColor: palette.border },
  appliedHeading: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: spacing.xs },
  appliedLabel: { color: palette.textSoft, fontSize: 11, fontWeight: '700' },
  count: { minWidth: 20, paddingHorizontal: 6, paddingVertical: 2, borderRadius: radius.pill, overflow: 'hidden', textAlign: 'center', color: palette.primaryDark, backgroundColor: palette.surfaceBlue, fontSize: 10, fontWeight: '700' },
  activeChips: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.xs },
  searchBox: { minHeight: 40, flexDirection: 'row', alignItems: 'center', gap: spacing.xs, marginHorizontal: spacing.md, marginTop: spacing.sm, paddingHorizontal: spacing.sm, borderWidth: 1, borderColor: palette.border, borderRadius: radius.sm, backgroundColor: palette.background },
  searchInput: { flex: 1, minWidth: 0, minHeight: 38, color: palette.text, fontSize: 13, outlineStyle: 'none' } as any,
  panelScroll: { flex: 1, minHeight: 0 },
  panelContent: { paddingHorizontal: spacing.md, paddingTop: spacing.xs, paddingBottom: spacing.md },
  section: { paddingVertical: spacing.xs, borderBottomWidth: 1, borderBottomColor: palette.border },
  sectionHeader: { minHeight: 45, flexDirection: 'row', alignItems: 'center', gap: spacing.xs },
  sectionName: { flex: 1, minWidth: 0 },
  sectionTitle: { color: palette.text, fontSize: 13, fontWeight: '700' },
  sectionSummary: { marginTop: 2, color: palette.textSoft, fontSize: 11 },
  options: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.xs, paddingBottom: spacing.xs },
  selectButton: { minHeight: 34, paddingHorizontal: spacing.sm, alignItems: 'center', justifyContent: 'center', borderRadius: radius.sm, borderWidth: 1, borderColor: palette.border },
  selectText: { color: palette.primaryDark, fontSize: 11, fontWeight: '700' },
  pharmacyLoading: { minHeight: 46, flexDirection: 'row', alignItems: 'center', gap: spacing.xs },
  actions: { flexDirection: 'row', gap: spacing.xs, padding: spacing.md, borderTopWidth: 1, borderTopColor: palette.border, backgroundColor: palette.surface },
  clearButton: { minWidth: 74, minHeight: 42, alignItems: 'center', justifyContent: 'center', paddingHorizontal: spacing.sm, borderWidth: 1, borderColor: palette.primary, borderRadius: radius.sm },
  clearText: { color: palette.primaryDark, fontSize: 12, fontWeight: '700' },
  applyButton: { flex: 1, minHeight: 42, alignItems: 'center', justifyContent: 'center', borderRadius: radius.sm, backgroundColor: palette.primary },
  applyText: { color: palette.onPrimary, fontSize: 12, fontWeight: '700' },
  pressed: { opacity: 0.8 },
  modalOverlay: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: spacing.xl, backgroundColor: palette.overlay },
  modal: { width: '100%', maxWidth: 560, height: '78%', padding: spacing.lg, borderRadius: radius.lg, backgroundColor: palette.surface },
  modalHeader: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, marginBottom: spacing.md },
  modalTitle: { color: palette.text, fontSize: 19, fontWeight: '800' },
  closeButton: { width: 40, height: 40, alignItems: 'center', justifyContent: 'center', borderRadius: radius.sm, backgroundColor: palette.surfaceAlt },
  labSearch: { minHeight: 44, flexDirection: 'row', alignItems: 'center', gap: spacing.sm, paddingHorizontal: spacing.sm, borderRadius: radius.sm, borderWidth: 1, borderColor: palette.border },
  labList: { flex: 1, marginTop: spacing.sm },
  emptyText: { paddingVertical: spacing.xl, textAlign: 'center', color: palette.textSoft, fontSize: 13 },
  labOption: { minHeight: 48, flexDirection: 'row', alignItems: 'center', gap: spacing.sm, paddingHorizontal: spacing.xs, borderBottomWidth: 1, borderBottomColor: palette.border },
  labOptionSelected: { backgroundColor: palette.surfaceBlue },
  checkbox: { width: 21, height: 21, alignItems: 'center', justifyContent: 'center', borderRadius: 5, borderWidth: 1.5, borderColor: palette.muted },
  checkboxActive: { borderColor: palette.primary, backgroundColor: palette.primary },
  labName: { flex: 1, minWidth: 0, color: palette.text, fontSize: 13, lineHeight: 18 },
  labNameActive: { color: palette.primaryDark, fontWeight: '600' },
  modalFooter: { flexDirection: 'row', gap: spacing.sm, paddingTop: spacing.md, borderTopWidth: 1, borderTopColor: palette.border },
  modalClear: { flex: 1, minHeight: 44, alignItems: 'center', justifyContent: 'center', borderWidth: 1, borderColor: palette.border, borderRadius: radius.sm },
  modalDone: { flex: 1, minHeight: 44, alignItems: 'center', justifyContent: 'center', borderRadius: radius.sm, backgroundColor: palette.primary },
}));
