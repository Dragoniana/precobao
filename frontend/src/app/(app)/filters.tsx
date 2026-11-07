import { Ionicons } from '@expo/vector-icons';
import { useRouter } from 'expo-router';
import React, { useEffect, useMemo, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  Modal,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  useWindowDimensions,
  View,
} from 'react-native';

import { FilterChip } from '../../components/FilterChip';
import { Screen } from '../../components/Screen';
import { FilterOption, useCatalogContext } from '../../context/AppContext';
import { api } from '../../services/api';
import { formatLaboratoryLabel, normalizeText } from '../../services/catalog';
import { palette, radius, spacing } from '../../theme';

type Pharmacy = { id: number; nome_fantasia: string };
type SectionKind = 'categoria' | 'tipo_venda';

function visibleFilters(filters: FilterOption[]) {
  return filters.filter((item) => !['principio_ativo', 'referencia', 'classificacao'].includes(String(item.type)));
}

const SECTION_LABELS: Record<SectionKind, string> = {
  categoria: 'Tipo de medicamento',
  tipo_venda: 'Tipo de venda',
};
const SECTION_ORDER: SectionKind[] = ['categoria', 'tipo_venda'];
const SECTION_ICONS: Record<SectionKind, keyof typeof Ionicons.glyphMap> = {
  categoria: 'medkit-outline',
  tipo_venda: 'receipt-outline',
};

export default function FiltersScreen() {
  const router = useRouter();
  const { width } = useWindowDimensions();
  const {
    selectedFilters, filterOptions, applyFilters, clearFilters,
    selectedPharmacyId, setSelectedPharmacyId,
  } = useCatalogContext();
  const isWeb = Platform.OS === 'web';
  const [search, setSearch] = useState('');
  const [laboratorySearch, setLaboratorySearch] = useState('');
  const [laboratoriesOpen, setLaboratoriesOpen] = useState(false);
  const [expanded, setExpanded] = useState<SectionKind[]>(['tipo_venda']);
  const [pharmaciesExpanded, setPharmaciesExpanded] = useState(false);
  const [temporaryFilters, setTemporaryFilters] = useState<FilterOption[]>(() => visibleFilters(selectedFilters));
  const [temporaryPharmacyId, setTemporaryPharmacyId] = useState<number | null>(selectedPharmacyId);
  const [pharmacies, setPharmacies] = useState<Pharmacy[]>([]);
  const [pharmaciesLoading, setPharmaciesLoading] = useState(true);

  useEffect(() => {
    setTemporaryFilters(visibleFilters(selectedFilters));
    setTemporaryPharmacyId(selectedPharmacyId);
  }, [selectedFilters, selectedPharmacyId]);

  useEffect(() => {
    let active = true;
    api.get<Pharmacy[]>('/catalogo/farmacias')
      .then(({ data }) => { if (active) setPharmacies(data); })
      .catch(() => { if (active) setPharmacies([]); })
      .finally(() => { if (active) setPharmaciesLoading(false); });
    return () => { active = false; };
  }, []);

  const goBackOrHome = () => router.canGoBack() ? router.back() : router.replace('/home');
  const filteredOptions = useMemo(() => {
    const needle = normalizeText(search);
    return filterOptions.filter((option) => !needle || normalizeText(option.label).includes(needle));
  }, [filterOptions, search]);
  const groupedOptions = useMemo(() => {
    const groups: Record<SectionKind, FilterOption[]> = {
      categoria: [], tipo_venda: [],
    };
    filteredOptions.forEach((option) => {
      if (option.type === 'categoria') {
        if (['similar', 'generico'].includes(normalizeText(option.value))) groups.categoria.push(option);
        return;
      }
      if (option.type === 'receita' || option.type === 'controle_especial') groups.tipo_venda.push(option);
    });
    return groups;
  }, [filteredOptions]);
  const selectedLaboratories = temporaryFilters.filter((item) => item.type === 'laboratorio');
  const laboratoryOptions = filterOptions.filter((item) => item.type === 'laboratorio');
  const matchingLaboratories = useMemo(() => {
    const needle = normalizeText(laboratorySearch);
    return laboratoryOptions.filter((option) => !needle || normalizeText(option.label).includes(needle));
  }, [laboratoryOptions, laboratorySearch]);
  const selectedPharmacy = pharmacies.find((item) => item.id === temporaryPharmacyId);
  const modalHeight = Math.min(620, Math.max(
    selectedLaboratories.length ? 420 : 380,
    220 + Math.min(matchingLaboratories.length, 7) * 48 + Math.min(selectedLaboratories.length, 3) * 48
  ));

  const isSelected = (option: FilterOption) => temporaryFilters.some((item) => item.id === option.id);
  const toggleFilter = (option: FilterOption) => setTemporaryFilters((current) => current.some((item) => item.id === option.id)
    ? current.filter((item) => item.id !== option.id)
    : [...current, option]);
  const toggleSection = (section: SectionKind) => setExpanded((current) => current.includes(section)
    ? current.filter((item) => item !== section) : [...current, section]);
  const handleApply = () => {
    applyFilters(visibleFilters(temporaryFilters));
    setSelectedPharmacyId(temporaryPharmacyId);
    goBackOrHome();
  };
  const handleClear = () => {
    setTemporaryFilters([]);
    setTemporaryPharmacyId(null);
    setSearch('');
    setLaboratorySearch('');
    clearFilters();
    setSelectedPharmacyId(null);
  };

  const chosenCount = temporaryFilters.length + Number(temporaryPharmacyId !== null);
  const appliedFilterIds = visibleFilters(selectedFilters).map((item) => item.id).sort().join('|');
  const draftFilterIds = temporaryFilters.map((item) => item.id).sort().join('|');
  const hasPendingChanges = appliedFilterIds !== draftFilterIds || temporaryPharmacyId !== selectedPharmacyId;
  const labSummary = selectedLaboratories.length
    ? `${selectedLaboratories.slice(0, 2).map((item) => item.label).join(', ')}${selectedLaboratories.length > 2 ? ` +${selectedLaboratories.length - 2}` : ''} · ${selectedLaboratories.length} selecionado(s)`
    : 'Todos os laboratórios';

  return (
    <Screen>
      <View style={[styles.form, isWeb && styles.formWeb, isWeb && width < 680 && styles.formMobile]}>
        <View style={styles.header}>
          <Pressable onPress={goBackOrHome} accessibilityRole="button" accessibilityLabel="Voltar" style={styles.backButton}>
            <Ionicons name="arrow-back" size={19} color={palette.primaryDark} />
          </Pressable>
          <View style={styles.headerCopy}>
            <Text style={styles.eyebrow}>REFINE SUA BUSCA</Text>
            <Text style={styles.title}>Filtros</Text>
            <Text style={styles.subtitle}>Escolha o que importa para comparar medicamentos.</Text>
          </View>
        </View>

        {chosenCount > 0 || selectedFilters.length > 0 || selectedPharmacyId !== null ? (
          <View style={styles.activeArea}>
            <View style={styles.activeHeading}>
              <Text style={styles.activeTitle}>Seleções atuais</Text>
              <Text style={[styles.draftStatus, hasPendingChanges && styles.draftStatusPending]}>{hasPendingChanges ? 'Alterações pendentes' : 'Aplicados'}</Text>
              <Text style={styles.activeCount}>{chosenCount}</Text>
            </View>
            <View style={styles.activeChips}>
              {temporaryFilters.map((option) => (
                <FilterChip key={`active-${option.id}`} label={option.type === 'laboratorio' ? option.label : option.label}
                  accessibilityLabel={`Remover ${option.label}`} compact active onPress={() => toggleFilter(option)} />
              ))}
              {selectedPharmacy && <FilterChip label={selectedPharmacy.nome_fantasia} accessibilityLabel={`Remover farmácia ${selectedPharmacy.nome_fantasia}`} compact active onPress={() => setTemporaryPharmacyId(null)} />}
            </View>
          </View>
        ) : null}

        <View style={styles.toolbar}>
          <View style={styles.globalSearch}>
            <Ionicons name="search-outline" size={17} color={palette.textSoft} />
            <TextInput accessibilityLabel="Buscar opção de filtro" value={search} onChangeText={setSearch}
              placeholder="Buscar opções" placeholderTextColor={palette.muted} autoCapitalize="none" autoCorrect={false}
              style={styles.searchInput} />
            {!!search && <Pressable accessibilityRole="button" accessibilityLabel="Limpar busca de filtros" onPress={() => setSearch('')} style={styles.iconButton}><Ionicons name="close-circle" size={18} color={palette.textSoft} /></Pressable>}
          </View>
          {!!search && <Text style={styles.searchHint}>A busca considera os grupos abaixo.</Text>}
        </View>

        <View style={styles.sections}>
          {SECTION_ORDER.map((section) => {
            const options = groupedOptions[section];
            if (!options.length) return null;
            const selected = section === 'tipo_venda'
              ? temporaryFilters.filter((item) => item.type === 'receita' || item.type === 'controle_especial')
              : temporaryFilters.filter((item) => item.type === section);
            const open = expanded.includes(section) || Boolean(search.trim());
            return (
              <View key={section} style={styles.section}>
                <Pressable accessibilityRole="button" accessibilityState={{ expanded: open }} onPress={() => toggleSection(section)} style={styles.sectionHead}>
                  <View style={styles.sectionIcon}><Ionicons name={SECTION_ICONS[section]} size={18} color={palette.primaryDark} /></View>
                  <View style={styles.sectionCopy}>
                    <Text style={styles.sectionTitle}>{SECTION_LABELS[section]}</Text>
                    <Text numberOfLines={1} style={styles.sectionSummary}>{selected.length ? selected.map((item) => item.label).join(', ') : `${options.length} opções`}</Text>
                  </View>
                  {!!selected.length && <Text style={styles.countBadge}>{selected.length}</Text>}
                  <Ionicons name={open ? 'chevron-up' : 'chevron-down'} size={18} color={palette.textSoft} />
                </Pressable>
                {open && <View style={styles.options}>
                  {options.map((option) => <FilterChip key={option.id} label={option.label} active={isSelected(option)} onPress={() => toggleFilter(option)} compact />)}
                </View>}
              </View>
            );
          })}

          <View style={styles.section}>
            <View style={styles.sectionHead}>
              <View style={styles.sectionIcon}><Ionicons name="flask-outline" size={18} color={palette.primaryDark} /></View>
              <View style={styles.sectionCopy}>
                <Text style={styles.sectionTitle}>Laboratórios</Text>
                <Text numberOfLines={1} style={styles.sectionSummary}>{labSummary}</Text>
              </View>
              {!!selectedLaboratories.length && <Text style={styles.countBadge}>{selectedLaboratories.length}</Text>}
              <Pressable accessibilityRole="button" onPress={() => setLaboratoriesOpen(true)} style={styles.selectButton}>
                <Text style={styles.selectButtonText}>Selecionar</Text>
              </Pressable>
            </View>
          </View>

          {pharmaciesLoading ? <View style={styles.loadingPharmacies}><ActivityIndicator size="small" color={palette.primary} /><Text style={styles.loadingText}>Carregando farmácias</Text></View> : pharmacies.length > 0 && (
            <View style={styles.section}>
              <Pressable accessibilityRole="button" accessibilityState={{ expanded: pharmaciesExpanded }} onPress={() => setPharmaciesExpanded((value) => !value)} style={styles.sectionHead}>
                <View style={styles.sectionIcon}><Ionicons name="storefront-outline" size={18} color={palette.primaryDark} /></View>
                <View style={styles.sectionCopy}><Text style={styles.sectionTitle}>Farmácia</Text><Text numberOfLines={1} style={styles.sectionSummary}>{selectedPharmacy?.nome_fantasia ?? 'Todas as farmácias'}</Text></View>
                <Ionicons name={pharmaciesExpanded ? 'chevron-up' : 'chevron-down'} size={18} color={palette.textSoft} />
              </Pressable>
              {pharmaciesExpanded && <View style={styles.options}>
                <FilterChip label="Todas" active={temporaryPharmacyId === null} onPress={() => setTemporaryPharmacyId(null)} compact />
                {pharmacies.map((pharmacy) => <FilterChip key={pharmacy.id} label={pharmacy.nome_fantasia} active={temporaryPharmacyId === pharmacy.id} onPress={() => setTemporaryPharmacyId(pharmacy.id)} compact />)}
              </View>}
            </View>
          )}
        </View>

        <View style={styles.actionsRow}>
          <Pressable accessibilityRole="button" onPress={handleClear} style={({ pressed }) => [styles.clearButton, pressed && styles.pressed]}><Text style={styles.clearText}>Limpar filtros</Text></Pressable>
          <Pressable accessibilityRole="button" onPress={handleApply} style={({ pressed }) => [styles.applyButton, pressed && styles.pressed]}><Text style={styles.applyText}>Aplicar filtros</Text></Pressable>
        </View>
      </View>

      <Modal visible={laboratoriesOpen} transparent={width >= 680} animationType={width < 680 ? 'slide' : 'fade'} presentationStyle={width < 680 ? 'pageSheet' : 'overFullScreen'} onRequestClose={() => setLaboratoriesOpen(false)}>
        <View style={[styles.modalOverlay, width < 680 && styles.modalOverlayMobile]}>
        <View style={[styles.modal, { height: modalHeight }, width < 680 && styles.modalMobile]}>
          <View style={styles.modalHeader}>
            <View style={styles.modalTitleWrap}><Text style={styles.modalTitle}>Laboratórios</Text><Text style={styles.modalSubtitle}>{selectedLaboratories.length} selecionado(s) de {laboratoryOptions.length}</Text></View>
            <Pressable accessibilityRole="button" accessibilityLabel="Fechar seletor de laboratórios" onPress={() => setLaboratoriesOpen(false)} style={styles.closeButton}><Ionicons name="close" size={20} color={palette.text} /></Pressable>
          </View>
          <View style={styles.labSearch}>
            <Ionicons name="search-outline" size={18} color={palette.textSoft} />
            <TextInput accessibilityLabel="Buscar laboratório" value={laboratorySearch} onChangeText={setLaboratorySearch}
              placeholder="Buscar laboratório..." placeholderTextColor={palette.muted} autoCapitalize="none" autoCorrect={false} returnKeyType="search" style={styles.searchInput} />
            {!!laboratorySearch && <Pressable accessibilityRole="button" accessibilityLabel="Limpar busca por laboratório" onPress={() => setLaboratorySearch('')} style={styles.iconButton}><Ionicons name="close-circle" size={18} color={palette.textSoft} /></Pressable>}
          </View>
          {!!selectedLaboratories.length && <View style={styles.modalSelected}>
            <Text style={styles.modalSelectedLabel}>Selecionados</Text>
            <View style={styles.modalSelectedChips}>
              {selectedLaboratories.slice(0, 3).map((option) => <FilterChip key={`modal-selected-${option.id}`} label={option.label} accessibilityLabel={`Remover ${option.label}`} compact active onPress={() => toggleFilter(option)} />)}
              {selectedLaboratories.length > 3 && <Text style={styles.modalSelectedMore}>+{selectedLaboratories.length - 3}</Text>}
            </View>
          </View>}
          <FlatList
            data={matchingLaboratories}
            keyExtractor={(option) => option.id}
            keyboardShouldPersistTaps="handled"
            style={styles.labList}
            contentContainerStyle={styles.labListContent}
            ListEmptyComponent={<Text style={styles.emptyText}>Nenhum laboratório encontrado.</Text>}
            renderItem={({ item: option }) => {
              const active = isSelected(option);
              return <Pressable accessibilityRole="checkbox" accessibilityState={{ checked: active }} onPress={() => toggleFilter(option)} style={[styles.labOption, active && styles.labOptionSelected]}>
                <View style={[styles.checkbox, active && styles.checkboxActive]}>{active && <Ionicons name="checkmark" size={15} color={palette.surface} />}</View>
                <Text numberOfLines={2} style={[styles.labOptionText, active && styles.labOptionTextSelected]}>{formatLaboratoryLabel(option.value)}</Text>
              </Pressable>;
            }}
          />
          <View style={styles.modalFooter}>
            <Pressable accessibilityRole="button" onPress={() => setTemporaryFilters((current) => current.filter((item) => item.type !== 'laboratorio'))} style={styles.modalClear}><Text style={styles.clearText}>Limpar seleção</Text></Pressable>
            <Pressable accessibilityRole="button" onPress={() => setLaboratoriesOpen(false)} style={styles.modalDone}><Text style={styles.applyText}>Concluir</Text></Pressable>
          </View>
        </View>
        </View>
      </Modal>
    </Screen>
  );
}

const styles = StyleSheet.create({
  form: { width: '100%' },
  formWeb: { maxWidth: 860, alignSelf: 'center' },
  formMobile: { width: 'calc(100% - 24px)' } as any,
  header: { flexDirection: 'row', alignItems: 'flex-start', gap: spacing.md, marginBottom: spacing.lg },
  backButton: { width: 42, height: 42, alignItems: 'center', justifyContent: 'center', borderRadius: radius.md, borderWidth: 1, borderColor: palette.border, backgroundColor: palette.surface },
  headerCopy: { flex: 1, minWidth: 0 },
  eyebrow: { color: palette.primaryDark, fontSize: 10, fontWeight: '800', letterSpacing: 1 },
  title: { marginTop: 3, color: palette.text, fontSize: 28, lineHeight: 34, fontWeight: '800' },
  subtitle: { marginTop: 3, color: palette.textSoft, fontSize: 14, lineHeight: 20 },
  activeArea: { marginBottom: spacing.md, paddingBottom: spacing.md, borderBottomWidth: 1, borderBottomColor: palette.border },
  activeHeading: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, marginBottom: spacing.sm },
  activeTitle: { color: palette.text, fontSize: 14, fontWeight: '700' },
  draftStatus: { marginLeft: 'auto', color: palette.textSoft, fontSize: 11, fontWeight: '600' },
  draftStatusPending: { color: palette.primaryDark },
  activeCount: { minWidth: 22, textAlign: 'center', overflow: 'hidden', color: palette.primaryDark, fontSize: 12, fontWeight: '700', backgroundColor: palette.surfaceBlue, borderRadius: radius.pill, paddingHorizontal: 6, paddingVertical: 2 },
  activeChips: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.xs },
  toolbar: { marginBottom: spacing.sm },
  globalSearch: { minHeight: 46, flexDirection: 'row', alignItems: 'center', gap: spacing.sm, paddingHorizontal: spacing.md, borderWidth: 1, borderColor: palette.border, borderRadius: radius.md, backgroundColor: palette.surface },
  searchInput: { flex: 1, minWidth: 0, minHeight: 44, color: palette.text, fontSize: 15, outlineStyle: 'none' } as any,
  iconButton: { width: 40, height: 42, alignItems: 'center', justifyContent: 'center' },
  searchHint: { marginTop: 5, color: palette.textSoft, fontSize: 12 },
  sections: { borderTopWidth: 1, borderTopColor: palette.border },
  section: { borderBottomWidth: 1, borderBottomColor: palette.border },
  sectionHead: { minHeight: 64, flexDirection: 'row', alignItems: 'center', gap: spacing.sm, paddingVertical: spacing.sm },
  sectionIcon: { width: 36, height: 36, alignItems: 'center', justifyContent: 'center', borderRadius: radius.sm, backgroundColor: palette.surfaceBlue },
  sectionCopy: { flex: 1, minWidth: 0 },
  sectionTitle: { color: palette.text, fontSize: 15, fontWeight: '700' },
  sectionSummary: { marginTop: 3, color: palette.textSoft, fontSize: 12, lineHeight: 17 },
  countBadge: { color: palette.primaryDark, backgroundColor: palette.surfaceBlue, borderRadius: radius.pill, paddingHorizontal: 8, paddingVertical: 4, fontSize: 11, fontWeight: '700', overflow: 'hidden' },
  options: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.xs, paddingBottom: spacing.md, paddingLeft: 44 },
  selectButton: { minHeight: 40, justifyContent: 'center', paddingHorizontal: spacing.md, borderWidth: 1, borderColor: palette.primary, borderRadius: radius.sm, backgroundColor: palette.surface },
  selectButtonText: { color: palette.primaryDark, fontSize: 13, fontWeight: '700' },
  loadingPharmacies: { minHeight: 60, flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  loadingText: { color: palette.textSoft, fontSize: 13 },
  actionsRow: { flexDirection: 'row', gap: spacing.sm, marginTop: spacing.lg, paddingBottom: spacing.md },
  clearButton: { flex: 1, minHeight: 48, justifyContent: 'center', alignItems: 'center', borderWidth: 1, borderColor: palette.primary, borderRadius: radius.sm, backgroundColor: palette.surface },
  clearText: { color: palette.primaryDark, fontSize: 14, fontWeight: '700' },
  applyButton: { flex: 1.25, minHeight: 48, justifyContent: 'center', alignItems: 'center', borderRadius: radius.sm, backgroundColor: palette.primary },
  applyText: { color: palette.surface, fontSize: 14, fontWeight: '700' },
  pressed: { opacity: 0.82 },
  modal: { width: '100%', maxWidth: 720, height: 580, maxHeight: '88%', alignSelf: 'center', marginTop: 'auto', marginBottom: 'auto', padding: spacing.lg, borderRadius: radius.lg, backgroundColor: palette.surface },
  modalMobile: { maxWidth: '100%', height: '100%', maxHeight: '100%', borderRadius: 0, paddingTop: spacing.xl },
  modalOverlay: { flex: 1, justifyContent: 'center', alignItems: 'center', padding: spacing.xl, backgroundColor: 'rgba(23, 32, 51, 0.38)' },
  modalOverlayMobile: { padding: 0, backgroundColor: palette.surface },
  modalHeader: { flexDirection: 'row', alignItems: 'center', gap: spacing.md, marginBottom: spacing.md },
  modalTitleWrap: { flex: 1 },
  modalTitle: { color: palette.text, fontSize: 21, fontWeight: '800' },
  modalSubtitle: { marginTop: 3, color: palette.textSoft, fontSize: 13 },
  closeButton: { width: 42, height: 42, alignItems: 'center', justifyContent: 'center', borderRadius: radius.sm, backgroundColor: palette.surfaceAlt },
  labSearch: { minHeight: 48, flexDirection: 'row', alignItems: 'center', gap: spacing.sm, paddingHorizontal: spacing.md, borderWidth: 1, borderColor: palette.border, borderRadius: radius.md, backgroundColor: palette.background },
  labList: { flex: 1, minHeight: 140, marginTop: spacing.md },
  labListContent: { paddingBottom: spacing.md },
  modalSelected: { marginTop: spacing.sm },
  modalSelectedLabel: { marginBottom: spacing.xs, color: palette.textSoft, fontSize: 12, fontWeight: '600' },
  modalSelectedChips: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'flex-start', gap: spacing.xs },
  modalSelectedMore: { alignSelf: 'center', color: palette.textSoft, fontSize: 12, fontWeight: '700' },
  labOption: { minHeight: 52, flexDirection: 'row', alignItems: 'center', gap: spacing.md, paddingHorizontal: spacing.sm, borderBottomWidth: 1, borderBottomColor: palette.border },
  labOptionSelected: { backgroundColor: palette.surfaceBlue },
  checkbox: { width: 22, height: 22, alignItems: 'center', justifyContent: 'center', borderWidth: 1.5, borderColor: palette.muted, borderRadius: 6, backgroundColor: palette.surface },
  checkboxActive: { borderColor: palette.primary, backgroundColor: palette.primary },
  labOptionText: { flex: 1, minWidth: 0, color: palette.text, fontSize: 14, lineHeight: 19 },
  labOptionTextSelected: { color: palette.primaryDark, fontWeight: '600' },
  emptyText: { paddingVertical: spacing.xl, textAlign: 'center', color: palette.textSoft, fontSize: 14 },
  modalFooter: { flexDirection: 'row', gap: spacing.sm, paddingTop: spacing.md, borderTopWidth: 1, borderTopColor: palette.border },
  modalClear: { flex: 1, minHeight: 48, justifyContent: 'center', alignItems: 'center', borderWidth: 1, borderColor: palette.border, borderRadius: radius.sm },
  modalDone: { flex: 1, minHeight: 48, justifyContent: 'center', alignItems: 'center', borderRadius: radius.sm, backgroundColor: palette.primary },
});
