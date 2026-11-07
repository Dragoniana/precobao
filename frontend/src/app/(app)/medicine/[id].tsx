import Ionicons from '@expo/vector-icons/Ionicons';
import { useLocalSearchParams, useRouter } from 'expo-router';
import React, { useEffect, useMemo, useState } from 'react';
import {
  Linking,
  Platform,
  Pressable,
  Text,
  useWindowDimensions,
  View,
} from 'react-native';

import { AuthRequiredModal } from '../../../components/AuthRequiredModal';
import { MedicineImage } from '../../../components/MedicineImage';
import { Screen } from '../../../components/Screen';
import { SearchBar } from '../../../components/SearchBar';
import { useCatalogContext, useFavoritesContext, useSessionContext } from '../../../context/AppContext';
import { getLaboratoryDisplayName } from '../../../services/catalog';
import { finishMedicineOpenTrace, markMedicineOpenTrace } from '../../../services/medicineOpenPerformance';
import { radius, spacing } from '../../../theme';
import { createThemedStyles, useAppTheme } from '../../../context/ThemeContext';
import { Medicamento, Oferta } from '../../../types/api';

type OfertaComExtras = Oferta & {
  farmacia_nome?: string;
  farmacia?: {
    nome_fantasia?: string;
    razao_social?: string;
  };
};

function getOfferPrice(offer?: Oferta) {
  if (!offer || offer.preco === null || offer.preco === undefined || offer.preco === '') return Infinity;
  const price = Number(offer.preco);
  return Number.isFinite(price) && price > 0 ? price : Infinity;
}

/** Keep the cheapest current offer from each pharmacy for this exact catalog medicine. */
export function getComparableOffers(offers: Oferta[] = []) {
  const cheapestByPharmacy = new Map<string, Oferta>();

  for (const offer of offers) {
    const price = getOfferPrice(offer);
    if (offer.disponivel === false || !Number.isFinite(price)) continue;

    const pharmacyName = getPharmacyName(offer as OfertaComExtras).trim();
    const pharmacyKey = offer.farmacia_id
      ? `id:${offer.farmacia_id}`
      : `name:${pharmacyName.toLocaleLowerCase()}`;
    const current = cheapestByPharmacy.get(pharmacyKey);
    if (!current || price < getOfferPrice(current)) cheapestByPharmacy.set(pharmacyKey, offer);
  }

  return [...cheapestByPharmacy.values()].sort((a, b) => getOfferPrice(a) - getOfferPrice(b));
}

export function formatPrice(value?: number) {
  if (value === undefined || !Number.isFinite(value)) {
    return 'Sem preço';
  }

  return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(value);
}

export function normalizeOfferUrl(url?: string) {
  if (!url) return '';

  const trimmedUrl = url.trim();
  if (trimmedUrl.startsWith('http://') || trimmedUrl.startsWith('https://')) {
    return trimmedUrl;
  }

  return `https://${trimmedUrl}`;
}

function getPharmacyName(offer: OfertaComExtras) {
  return (
    offer.farmacia_nome ||
    offer.farmacia?.nome_fantasia ||
    offer.farmacia?.razao_social ||
    'Farmácia participante'
  );
}

export function getMedicineDetails(medicine?: Medicamento, laboratoryName?: string) {
  return [
    { label: 'Princ\u00edpio ativo', value: medicine?.principio_ativo },
    { label: 'Laborat\u00f3rio', value: laboratoryName || medicine?.laboratorio },
    { label: 'C\u00f3digo de barras', value: medicine?.codigo_barras },
  ].filter((item): item is { label: string; value: string } => Boolean(item.value?.trim()));
}

export default function MedicineDetailScreen() {
  const styles = useStyles();
  const { palette } = useAppTheme();
  const router = useRouter();
  const params = useLocalSearchParams<{ id?: string | string[] }>();

  const { getMedicineById, loadMedicineById, markAsViewed } = useCatalogContext();
  const { favoriteIds, toggleFavorite } = useFavoritesContext();
  const { sessionMode } = useSessionContext();

  const [search, setSearch] = useState('');
  const [showAuthModal, setShowAuthModal] = useState(false);
  const [loadedMedicine, setLoadedMedicine] = useState<Medicamento | undefined>();
  const [loadingMedicine, setLoadingMedicine] = useState(true);
  const [medicineLoadError, setMedicineLoadError] = useState(false);

  const { width } = useWindowDimensions();

  const medicineId = useMemo(() => {
    const rawId = Array.isArray(params.id) ? params.id[0] : params.id;
    const parsedId = Number(rawId);

    return Number.isFinite(parsedId) ? parsedId : null;
  }, [params.id]);

  const cachedMedicine = medicineId === null ? undefined : getMedicineById(medicineId);
  const medicine = loadedMedicine?.id === medicineId ? loadedMedicine : cachedMedicine;
  const hasMedicine = Boolean(medicine);

  useEffect(() => {
    let active = true;
    if (medicineId !== null) markMedicineOpenTrace(medicineId, 'detail-mounted');
    if (medicineId === null) {
      setLoadedMedicine(undefined);
      setLoadingMedicine(false);
      return;
    }
    setMedicineLoadError(false);
    setLoadingMedicine(true);
    loadMedicineById(medicineId, true).then((item) => {
      if (active) { setLoadedMedicine(item); setMedicineLoadError(false); }
    }).catch(() => { if (active) setMedicineLoadError(true); })
      .finally(() => { if (active) setLoadingMedicine(false); });
    return () => { active = false; };
  }, [loadMedicineById, medicineId]);

  useEffect(() => {
    if (medicineId === null) return;
    markMedicineOpenTrace(medicineId, hasMedicine ? 'content-committed' : 'loading-shell-committed');
  }, [hasMedicine, medicine?.id, medicineId]);

  useEffect(() => {
    if (medicineId === null || !medicine) return;
    markMedicineOpenTrace(medicineId, loadedMedicine?.id === medicineId ? 'content-usable' : 'cached-content-usable');
    if (!loadingMedicine && (loadedMedicine?.id === medicineId || medicineLoadError)) {
      finishMedicineOpenTrace(medicineId, 'content-ready');
    }
  }, [loadedMedicine, loadingMedicine, medicine, medicineId, medicineLoadError]);

  useEffect(() => {
    if (medicineId !== null) {
      markAsViewed(medicineId);
    }
  }, [medicineId, markAsViewed]);

  const offers = useMemo(() => {
    if (!medicine?.ofertas?.length) {
      return [];
    }

    return getComparableOffers(medicine.ofertas);
  }, [medicine]);

  const lowest = offers[0];
  const isFavorite = medicineId !== null && favoriteIds.includes(medicineId);

  const isWeb = Platform.OS === 'web';
  const isDesktop = isWeb && width >= 1100;
  const isTablet = isWeb && width >= 850 && width < 1100;
  const laboratoryName = getLaboratoryDisplayName(medicine?.laboratorio);

  const productUrl = normalizeOfferUrl(lowest?.url_origem);
  const lowestPrice = lowest ? Number(lowest.preco) : undefined;
  const medicineDetails = getMedicineDetails(medicine, laboratoryName);


  const handleToggleFavorite = () => {
    if (sessionMode !== 'authenticated') {
      setShowAuthModal(true);
      return;
    }

    if (medicineId !== null) {
      toggleFavorite(medicineId);
    }
  };

  const handleOpenProductSite = async (url?: string) => {
    const normalized = normalizeOfferUrl(url);

    if (!normalized) {
      return;
    }

    try {
      const canOpen = await Linking.canOpenURL(normalized);

      if (canOpen) {
        await Linking.openURL(normalized);
      }
    } catch (error) {
      console.error('Erro ao abrir site do remédio:', error);
    }
  };

  if (!medicine) {
    return (
      <Screen>
        <SearchBar
          value={search}
          onChangeText={setSearch}
          onSubmit={() => router.push({ pathname: '/search', params: { q: search } })}
          onBack={() => router.back()}
        />

        {loadingMedicine ? (
          <View style={styles.loadingDetail} accessibilityRole="progressbar" accessibilityLabel="Carregando detalhes do medicamento">
            <View style={styles.loadingImage} />
            <View style={styles.loadingLine} />
            <View style={styles.loadingLineShort} />
            <Text style={styles.notFoundText}>Carregando detalhes do medicamento...</Text>
          </View>
        ) : (
          <View style={styles.notFoundBox}>
            <Ionicons name="alert-circle-outline" size={42} color={palette.primary} />
            <Text style={styles.notFoundTitle}>Medicamento não encontrado</Text>
            <Text style={styles.notFoundText}>
              {medicineLoadError ? 'Não foi possível carregar este medicamento. Verifique a conexão e tente novamente.' : 'Esse medicamento não foi encontrado no catálogo.'}
            </Text>
          </View>
        )}
      </Screen>
    );
  }

  return (
    <Screen>
      <SearchBar
        value={search}
        onChangeText={setSearch}
        onSubmit={() => router.push({ pathname: '/search', params: { q: search } })}
        onBack={() => router.back()}
      />

      <View style={[styles.layout, isDesktop && styles.layoutDesktop]}>
        <View style={[styles.mainColumn, isDesktop && styles.mainColumnDesktop]}>
          <View style={[styles.productOverview, isDesktop && styles.productOverviewDesktop]}>
            <MedicineImage medicine={medicine} style={[styles.imageCard, isTablet && styles.imageCardTablet, isDesktop && styles.imageCardDesktop]} onLoad={() => markMedicineOpenTrace(medicine.id, 'image-loaded')} />

            <View style={[styles.detailsColumn, isDesktop && styles.detailsColumnDesktop]}>
              <View style={[styles.headerRow, isDesktop && styles.headerRowDesktop]}>
                <View style={styles.infoBlock}>
                  <Text accessibilityRole="header" style={[styles.name, isDesktop && styles.nameDesktop]}>
                    {medicine.nome}
                  </Text>
                  {lowest ? <Text style={[styles.price, isDesktop && styles.priceDesktop]}>{formatPrice(lowestPrice)}</Text> : null}
                  <Text style={styles.lowestStoreText}>
                    {lowest
                      ? `Menor pre\u00e7o em ${getPharmacyName(lowest as OfertaComExtras)}`
                      : 'Nenhuma oferta dispon\u00edvel'}
                  </Text>
                  {loadingMedicine ? <Text style={styles.refreshStatus}>Atualizando ofertas...</Text> : null}
                  {medicineLoadError ? <Text style={styles.refreshStatus}>Exibindo dados anteriores; não foi possível atualizar as ofertas.</Text> : null}
                </View>

                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel={isFavorite ? 'Remover dos favoritos' : 'Adicionar aos favoritos'}
                  accessibilityState={{ selected: isFavorite }}
                  style={styles.favoriteButton}
                  onPress={handleToggleFavorite}
                >
                  <Ionicons
                    name={isFavorite ? 'heart' : 'heart-outline'}
                    size={24}
                    color={isFavorite ? palette.danger : palette.text}
                  />
                </Pressable>
              </View>

              {medicineDetails.length > 0 ? (
                <View style={styles.descriptionSection}>
                  <Text style={styles.sectionLabel}>{'Sobre o medicamento'}</Text>
                  <View style={styles.descriptionBox}>
                    {medicineDetails.map((item) => (
                      <View key={item.label} style={styles.descriptionRow}>
                        <Text style={styles.descriptionLabel}>{item.label}</Text>
                        <Text style={styles.descriptionValue}>{item.value}</Text>
                      </View>
                    ))}
                  </View>
                </View>
              ) : null}

              <View style={styles.buttonGroup}>
                <Pressable
                  accessibilityRole="link"
                  style={[styles.siteButton, !productUrl && styles.disabledButton]}
                  disabled={!productUrl}
                  onPress={() => handleOpenProductSite(productUrl)}
                >
                  <Ionicons name="open-outline" size={19} color={palette.surface} />
                  <Text style={styles.siteButtonText}>{'Ir para o site do rem\u00e9dio'}</Text>
                </Pressable>

                <Pressable
                  accessibilityRole="button"
                  style={styles.historyButton}
                  onPress={() => router.push(`/history/${medicine.id}`)}
                >
                  <Ionicons name="stats-chart-outline" size={19} color={palette.primary} />
                  <Text style={styles.historyButtonText}>{'Ver hist\u00f3rico de pre\u00e7os'}</Text>
                </Pressable>
              </View>
            </View>
          </View>
        </View>

        <View style={[styles.offersPanel, isDesktop && styles.offersPanelDesktop]}>
          <View style={styles.offersHeader}>
            <Text style={styles.offersTitle}>Compare preços nas farmácias</Text>
            {offers.length > 0 ? (
              <Text style={styles.offersSubtitle}>
                {offers.length === 1 ? 'Encontramos esta oferta para o medicamento.' : `${offers.length} farmácias disponíveis`}
              </Text>
            ) : null}
          </View>

          {offers.length === 0 ? (
            <Text style={styles.emptyOffers}>Nenhuma oferta com preço atual disponível no momento.</Text>
          ) : (
            <View style={styles.offersList}>
              {offers.map((offer, index) => {
                const pharmacyName = getPharmacyName(offer as OfertaComExtras);
                const offerUrl = normalizeOfferUrl(offer.url_origem);
                const updatedAt = offer.atualizado_em;
                const updatedDate = updatedAt ? new Date(updatedAt) : null;
                const formattedDate = updatedDate && !Number.isNaN(updatedDate.getTime())
                  ? updatedDate.toLocaleDateString('pt-BR')
                  : null;
                const isLowest = index === 0;

                return (
                  <View key={offer.id} style={[styles.offerRow, isLowest && styles.offerRowLowest]}>
                    <View style={styles.offerLeft}>
                      <View style={styles.offerInfo}>
                        <View style={styles.offerNameRow}>
                          <Text style={styles.offerName} numberOfLines={2}>{pharmacyName}</Text>
                          {isLowest ? <Text style={styles.bestPriceBadge}>Menor preço</Text> : null}
                        </View>
                        <Text style={styles.offerPrice}>{formatPrice(getOfferPrice(offer))}</Text>
                        {formattedDate ? <Text style={styles.offerUpdated}>Atualizado em {formattedDate}</Text> : null}
                      </View>
                    </View>
                    {offerUrl ? (
                      <Pressable accessibilityRole="link" style={styles.offerSiteButton} onPress={() => handleOpenProductSite(offerUrl)}>
                        <Text style={styles.offerSiteButtonText}>Ver oferta</Text>
                      </Pressable>
                    ) : null}
                  </View>
                );
              })}
            </View>
          )}
        </View>
      </View>

      <AuthRequiredModal
        visible={showAuthModal}
        onClose={() => setShowAuthModal(false)}
      />
    </Screen>
  );
}

const useStyles = createThemedStyles((palette) => ({
  layout: { marginTop: spacing.md, gap: spacing.lg },
  layoutDesktop: { flexDirection: 'row', alignItems: 'flex-start' },
  mainColumn: {
    backgroundColor: palette.surface,
    borderRadius: radius.xl,
    borderWidth: 1,
    borderColor: palette.border,
    padding: spacing.md,
  },
  mainColumnDesktop: { flex: 1, minWidth: 0, padding: spacing.xl },
  productOverview: { gap: spacing.lg },
  productOverviewDesktop: { flexDirection: 'row', alignItems: 'flex-start', gap: spacing.xl },
  detailsColumn: { minWidth: 0 },
  detailsColumnDesktop: { flex: 1 },
  imageCard: {
    width: '100%',
    aspectRatio: 1.55,
    borderRadius: radius.lg,
    backgroundColor: palette.surfaceAlt,
    justifyContent: 'center',
    alignItems: 'center',
    overflow: 'hidden',
  },
  imageCardTablet: { aspectRatio: 1.7 },
  imageCardDesktop: { width: '38%', height: 320, aspectRatio: undefined, maxWidth: 360, flexShrink: 0 },
  headerRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    marginTop: spacing.xs,
    gap: spacing.md,
  },
  headerRowDesktop: { marginTop: 0 },
  infoBlock: { flex: 1, minWidth: 0 },
  favoriteButton: {
    width: 48,
    height: 48,
    flexShrink: 0,
    borderRadius: radius.md,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: palette.surfaceBlue,
    borderWidth: 1,
    borderColor: palette.border,
  },
  name: { fontSize: 24, color: palette.text, fontWeight: '500', marginBottom: spacing.xs },
  nameDesktop: { fontSize: 32, lineHeight: 38 },
  price: { fontSize: 24, color: palette.text, fontWeight: '700' },
  priceDesktop: { fontSize: 30 },
  lowestStoreText: { marginTop: 8, color: palette.textSoft, fontSize: 14, lineHeight: 20 },
  descriptionSection: { marginTop: spacing.lg },
  sectionLabel: { fontSize: 13, fontWeight: '700', color: palette.text, marginBottom: spacing.xs },
  descriptionBox: { borderTopWidth: 1, borderTopColor: palette.border },
  descriptionRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: spacing.sm,
    paddingVertical: spacing.xs,
    borderBottomWidth: 1,
    borderBottomColor: palette.border,
  },
  descriptionLabel: { width: 104, flexShrink: 0, color: palette.textSoft, fontSize: 12, lineHeight: 19 },
  descriptionValue: { flex: 1, minWidth: 0, color: palette.text, fontSize: 13, fontWeight: '500', lineHeight: 19 },
  buttonGroup: { gap: spacing.md, marginTop: spacing.lg },
  siteButton: {
    minHeight: 52,
    paddingHorizontal: spacing.md,
    borderRadius: radius.md,
    backgroundColor: palette.primary,
    justifyContent: 'center',
    alignItems: 'center',
    flexDirection: 'row',
    gap: spacing.sm,
  },
  siteButtonText: { color: palette.onPrimary, fontSize: 15, fontWeight: '700' },
  historyButton: {
    minHeight: 52,
    paddingHorizontal: spacing.md,
    borderRadius: radius.md,
    backgroundColor: palette.surface,
    borderWidth: 1.5,
    borderColor: palette.primary,
    justifyContent: 'center',
    alignItems: 'center',
    flexDirection: 'row',
    gap: spacing.sm,
  },
  historyButtonText: { color: palette.primary, fontSize: 15, fontWeight: '700' },
  disabledButton: { backgroundColor: palette.muted },
  offersPanel: {
    backgroundColor: palette.surface,
    borderRadius: radius.xl,
    borderWidth: 1,
    borderColor: palette.border,
    padding: spacing.md,
  },
  offersPanelDesktop: {
    width: 360,
    flexShrink: 0,
    borderRadius: radius.xl,
    padding: spacing.lg,
    position: 'sticky' as any,
    top: 24,
  },
  offersHeader: { marginBottom: spacing.sm },
  offersTitle: { fontSize: 20, fontWeight: '700', color: palette.text, marginBottom: 4 },
  offersSubtitle: { fontSize: 13, color: palette.textSoft },
  emptyOffers: { color: palette.textSoft, fontSize: 14, lineHeight: 21, padding: spacing.md },
  loadingDetail: { gap: spacing.md, padding: spacing.lg, backgroundColor: palette.surface, borderRadius: radius.lg, borderWidth: 1, borderColor: palette.border },
  loadingImage: { width: '100%', aspectRatio: 1.7, borderRadius: radius.md, backgroundColor: palette.surfaceAlt },
  loadingLine: { width: '78%', height: 20, borderRadius: radius.sm, backgroundColor: palette.surfaceAlt },
  loadingLineShort: { width: '48%', height: 14, borderRadius: radius.sm, backgroundColor: palette.surfaceAlt },
  refreshStatus: { marginTop: 5, color: palette.textSoft, fontSize: 12, lineHeight: 17 },
  offersList: { gap: spacing.sm },
  offerRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    paddingVertical: spacing.md,
    paddingHorizontal: spacing.xs,
    borderRadius: 0,
    backgroundColor: 'transparent',
    borderBottomWidth: 1,
    borderBottomColor: palette.border,
  },
  offerRowLowest: { backgroundColor: palette.surfaceBlue, borderRadius: radius.md, borderBottomWidth: 0, paddingHorizontal: spacing.sm },
  offerLeft: { flexDirection: 'row', alignItems: 'flex-start', gap: spacing.sm, flex: 1, minWidth: 0 },
  offerInfo: { flex: 1, minWidth: 0 },
  offerNameRow: { flexDirection: 'row', alignItems: 'center', flexWrap: 'wrap', gap: spacing.xs, marginBottom: 6 },
  offerName: { flexShrink: 1, fontSize: 14, color: palette.text, fontWeight: '600' },
  bestPriceBadge: { overflow: 'hidden', borderRadius: radius.sm, backgroundColor: palette.primary, paddingHorizontal: 7, paddingVertical: 3, color: palette.onPrimary, fontSize: 10, fontWeight: '700' },
  offerPrice: { fontSize: 20, fontWeight: '700', color: palette.text },
  offerUpdated: { marginTop: 3, color: palette.textSoft, fontSize: 11 },
  offerSiteButton: {
    minHeight: 38,
    minWidth: 92,
    paddingHorizontal: spacing.sm,
    borderRadius: radius.sm,
    backgroundColor: palette.primary,
    justifyContent: 'center',
    alignItems: 'center',
  },
  offerSiteButtonText: { color: palette.onPrimary, fontSize: 13, fontWeight: '700' },
  notFoundBox: { alignItems: 'center', justifyContent: 'center', paddingVertical: 80, gap: spacing.md },
  notFoundTitle: { fontSize: 24, color: palette.text, fontWeight: '700' },
  notFoundText: { fontSize: 15, color: palette.textSoft, textAlign: 'center' },
}));
