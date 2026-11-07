import { Ionicons } from '@expo/vector-icons';
import { useLocalSearchParams, useRouter } from 'expo-router';
import React, { useEffect, useMemo, useState } from 'react';
import {
  Linking,
  Platform,
  Pressable,
  StyleSheet,
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
import { palette, radius, spacing } from '../../../theme';
import { Medicamento, Oferta } from '../../../types/api';

type OfertaComExtras = Oferta & {
  farmacia_nome?: string;
  farmacia?: {
    nome_fantasia?: string;
    razao_social?: string;
  };
};

function getOfferPrice(offer?: Oferta) {
  if (!offer) return Infinity;
  return Number(offer.preco);
}

function formatPrice(value?: number) {
  if (value === undefined || !Number.isFinite(value)) {
    return 'Sem preço';
  }

  return `R$ ${value.toFixed(2).replace('.', ',')}`;
}

function normalizeUrl(url?: string) {
  if (!url) return '';

  if (url.startsWith('http://') || url.startsWith('https://')) {
    return url;
  }

  return `https://${url}`;
}

function getPharmacyName(offer: OfertaComExtras) {
  return (
    offer.farmacia_nome ||
    offer.farmacia?.nome_fantasia ||
    offer.farmacia?.razao_social ||
    'Farmácia participante'
  );
}

export default function MedicineDetailScreen() {
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

  const cachedMedicine = useMemo(() => {
    if (medicineId === null) {
      return undefined;
    }

    return getMedicineById(medicineId);
  }, [getMedicineById, medicineId]);

  const medicine = cachedMedicine || loadedMedicine;

  useEffect(() => {
    let active = true;
    if (medicineId === null) {
      setLoadedMedicine(undefined);
      setLoadingMedicine(false);
      return;
    }
    setLoadedMedicine(undefined);
    setMedicineLoadError(false);
    setLoadingMedicine(true);
    loadMedicineById(medicineId).then((item) => {
      if (active) { setLoadedMedicine(item); setMedicineLoadError(false); }
    }).catch(() => { if (active) setMedicineLoadError(true); })
      .finally(() => { if (active) setLoadingMedicine(false); });
    return () => { active = false; };
  }, [loadMedicineById, medicineId]);

  useEffect(() => {
    if (medicineId !== null) {
      markAsViewed(medicineId);
    }
  }, [medicineId, markAsViewed]);

  const offers = useMemo(() => {
    if (!medicine?.ofertas?.length) {
      return [];
    }

    return medicine.ofertas.filter((offer) => offer.disponivel !== false).sort((a, b) => getOfferPrice(a) - getOfferPrice(b));
  }, [medicine]);

  const lowest = offers[0];
  const isFavorite = medicineId !== null && favoriteIds.includes(medicineId);

  const isWeb = Platform.OS === 'web';
  const isDesktop = isWeb && width >= 1100;
  const isTablet = isWeb && width >= 850 && width < 1100;
  const laboratoryName = getLaboratoryDisplayName(medicine?.laboratorio);

  const productUrl = normalizeUrl(lowest?.url_origem);
  const lowestPrice = lowest ? Number(lowest.preco) : undefined;


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
    const normalized = normalizeUrl(url);

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

  if (!medicine && loadingMedicine) {
    return <Screen><View style={styles.notFoundBox}><Text style={styles.notFoundText}>Carregando medicamento...</Text></View></Screen>;
  }

  if (!medicine) {
    return (
      <Screen>
        <SearchBar
          value={search}
          onChangeText={setSearch}
          onSubmit={() => router.push({ pathname: '/search', params: { q: search } })}
          onBack={() => router.back()}
        />

        <View style={styles.notFoundBox}>
          <Ionicons name="alert-circle-outline" size={42} color={palette.primary} />
          <Text style={styles.notFoundTitle}>Medicamento não encontrado</Text>
          <Text style={styles.notFoundText}>
            {medicineLoadError ? 'Não foi possível carregar este medicamento. Verifique a conexão e tente novamente.' : 'Esse medicamento não foi encontrado no catálogo.'}
          </Text>
        </View>
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
        <View style={styles.mainColumn}>
          <MedicineImage medicine={medicine} style={[styles.imageCard, isTablet && styles.imageCardTablet, isDesktop && styles.imageCardDesktop]} />

          <View style={styles.headerRow}>
            <View style={styles.infoBlock}>
              <Text numberOfLines={3} ellipsizeMode="tail" style={[styles.name, isDesktop && styles.nameDesktop]}>
                {medicine.nome}
              </Text>

              {lowest ? <Text style={[styles.price, isDesktop && styles.priceDesktop]}>{formatPrice(lowestPrice)}</Text> : null}

              {lowest ? (
                <Text style={styles.lowestStoreText}>
                  Menor preço em {getPharmacyName(lowest as OfertaComExtras)}
                </Text>
              ) : (
                <Text style={styles.lowestStoreText}>Nenhuma oferta disponível</Text>
              )}
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
                size={30}
                color={isFavorite ? palette.danger : palette.text}
              />
            </Pressable>
          </View>

          <Text style={styles.sectionLabel}>Descrição</Text>

          <View style={styles.descriptionBox}>
            <Text style={[styles.description, isDesktop && styles.descriptionDesktop]}>
              Princípio ativo: {medicine.principio_ativo || 'Não informado'}
            </Text>
            <Text style={[styles.description, isDesktop && styles.descriptionDesktop]}>
              Laboratório: {laboratoryName || 'Não informado'}
            </Text>
            <Text style={[styles.description, isDesktop && styles.descriptionDesktop]}>
              Código de barras: {medicine.codigo_barras || 'Não informado'}
            </Text>
            <Text style={[styles.description, isDesktop && styles.descriptionDesktop]}>
              {medicine.categoria_regulatoria
                ? `Categoria ANVISA: ${medicine.categoria_regulatoria}.`
                : 'Categoria regulatória não confirmada pela ANVISA.'}
            </Text>
            <Text style={[styles.description, isDesktop && styles.descriptionDesktop]}>
              {medicine.referencia_anvisa === true
                ? 'Consta na lista ANVISA de medicamentos de referência.'
                : medicine.referencia_anvisa === false
                  ? 'Não consta na lista ANVISA de medicamentos de referência.'
                  : 'Status como medicamento de referência não confirmado.'}
            </Text>
            <Text style={[styles.description, isDesktop && styles.descriptionDesktop]}>
              {medicine.exige_receita_confirmada === true
                ? 'Receita confirmada na fonte CMED consultada.'
                : medicine.exige_receita_confirmada === false
                  ? 'Venda sem receita confirmada na fonte consultada.'
                  : 'Exigência de receita não confirmada na fonte consultada.'}
            </Text>
          </View>

          <View style={styles.buttonGroup}>
            <Pressable
              style={[
                styles.siteButton,
                !productUrl && styles.disabledButton,
                isDesktop && styles.actionButtonDesktop,
              ]}
              disabled={!productUrl}
              onPress={() => handleOpenProductSite(productUrl)}
            >
              <Ionicons name="open-outline" size={20} color={palette.surface} />
              <Text style={styles.siteButtonText}>Ir para o site do remédio</Text>
            </Pressable>

            <Pressable
              style={[styles.historyButton, isDesktop && styles.actionButtonDesktop]}
              onPress={() => router.push(`/history/${medicine.id}`)}
            >
              <Ionicons name="stats-chart-outline" size={20} color={palette.primary} />
              <Text style={styles.historyButtonText}>Ver histórico de preços</Text>
            </Pressable>
          </View>
        </View>

        {offers.length !== 1 ? <View style={[styles.offersPanel, isDesktop && styles.offersPanelDesktop]}>
          {offers.length > 1 ? (
            <View style={styles.offersHeader}>
              <Text style={styles.offersTitle}>Comparar ofertas</Text>
              <Text style={styles.offersSubtitle}>{offers.length} farmácias disponíveis</Text>
            </View>
          ) : null}

          {lowest ? (
            <View style={styles.lowestBox}>
              <Text style={styles.lowestLabel}>Menor preço</Text>
              <Text style={styles.lowestPharmacy}>{getPharmacyName(lowest as OfertaComExtras)}</Text>
              <Text style={styles.lowestPrice}>{formatPrice(Number(lowest.preco))}</Text>
            </View>
          ) : (
            <Text style={styles.emptyOffers}>Nenhuma oferta disponível no momento.</Text>
          )}

          {offers.length > 1 ? <View style={styles.offersList}>
            {offers.slice(1).map((offer) => {
              const pharmacyName = getPharmacyName(offer as OfertaComExtras);
              const offerUrl = normalizeUrl(offer.url_origem);
              return (
                <View key={offer.id} style={styles.offerRow}>
                  <View style={styles.offerLeft}>
                    <View style={styles.offerInfo}>
                      <Text style={styles.offerName} numberOfLines={1}>{pharmacyName}</Text>
                      <Text style={styles.offerPrice}>{formatPrice(Number(offer.preco))}</Text>
                    </View>
                  </View>
                  <Pressable style={[styles.offerSiteButton, !offerUrl && styles.disabledSmallButton]} disabled={!offerUrl} onPress={() => handleOpenProductSite(offerUrl)}>
                    <Text style={styles.offerSiteButtonText}>Abrir site</Text>
                  </Pressable>
                </View>
              );
            })}
          </View> : null}
        </View> : null}
      </View>

      <AuthRequiredModal
        visible={showAuthModal}
        onClose={() => setShowAuthModal(false)}
      />
    </Screen>
  );
}

const styles = StyleSheet.create({
  layout: {
    marginTop: spacing.md,
    gap: spacing.xl,
  },
  layoutDesktop: {
    flexDirection: 'row',
    alignItems: 'flex-start',
  },
  mainColumn: {
    flex: 1,
  },
  imageCard: {
    width: '100%',
    aspectRatio: 1.35,
    borderRadius: radius.lg,
    backgroundColor: palette.surface,
    justifyContent: 'center',
    alignItems: 'center',
    overflow: 'hidden',
  },
  imageCardTablet: {
    aspectRatio: 1.55,
  },
  imageCardDesktop: {
    height: 340,
    aspectRatio: undefined,
    maxWidth: 760,
  },
  headerRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    marginTop: spacing.lg,
    gap: spacing.md,
  },
  infoBlock: {
    flex: 1,
    minWidth: 0,
  },
  favoriteButton: {
    width: 48,
    height: 48,
    borderRadius: radius.pill,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: palette.surface,
    borderWidth: 1,
    borderColor: '#E8E8E8',
  },
  name: {
    fontSize: 24,
    color: palette.text,
    fontWeight: '500',
    marginBottom: spacing.xs,
  },
  nameDesktop: {
    fontSize: 42,
    lineHeight: 48,
  },
  price: {
    fontSize: 24,
    color: palette.text,
    fontWeight: '700',
  },
  priceDesktop: {
    fontSize: 30,
  },
  lowestStoreText: {
    marginTop: 8,
    color: palette.textSoft,
    fontSize: 15,
  },
  sectionLabel: {
    fontSize: 20,
    fontWeight: '600',
    color: palette.text,
    marginTop: spacing.xl,
    marginBottom: spacing.sm,
  },
  descriptionBox: {
    gap: 6,
    maxWidth: 760,
  },
  description: {
    color: palette.textSoft,
    fontSize: 14,
    lineHeight: 21,
  },
  descriptionDesktop: {
    fontSize: 16,
    lineHeight: 28,
  },
  buttonGroup: {
    gap: spacing.md,
    marginTop: spacing.xl,
  },
  actionButtonDesktop: {
    maxWidth: 760,
  },
  siteButton: {
    minHeight: 52,
    borderRadius: radius.pill,
    backgroundColor: palette.primary,
    justifyContent: 'center',
    alignItems: 'center',
    flexDirection: 'row',
    gap: spacing.sm,
  },
  siteButtonText: {
    color: palette.surface,
    fontSize: 16,
    fontWeight: '700',
  },
  historyButton: {
    minHeight: 52,
    borderRadius: radius.pill,
    backgroundColor: palette.surface,
    borderWidth: 1.5,
    borderColor: palette.primary,
    justifyContent: 'center',
    alignItems: 'center',
    flexDirection: 'row',
    gap: spacing.sm,
  },
  historyButtonText: {
    color: palette.primary,
    fontSize: 16,
    fontWeight: '700',
  },
  disabledButton: {
    backgroundColor: palette.muted,
  },
  offersPanel: {
    backgroundColor: 'transparent',
    borderRadius: 0,
    borderWidth: 0,
    padding: 0,
  },
  offersPanelDesktop: {
    width: 390,
    position: 'sticky' as any,
    top: 24,
  },
  offersHeader: {
    marginBottom: spacing.md,
  },
  offersTitle: {
    fontSize: 22,
    fontWeight: '700',
    color: palette.text,
    marginBottom: 4,
  },
  offersSubtitle: {
    fontSize: 13,
    color: palette.textSoft,
  },
  emptyOffers: { color: palette.textSoft, fontSize: 14, lineHeight: 21, padding: spacing.md },
  lowestBox: {
    borderRadius: radius.md,
    backgroundColor: palette.backgroundSoft,
    borderWidth: 0,
    padding: spacing.md,
    marginBottom: spacing.md,
  },
  lowestLabel: {
    fontSize: 12,
    color: palette.textSoft,
    marginBottom: 4,
  },
  lowestPharmacy: {
    fontSize: 16,
    fontWeight: '700',
    color: palette.primary,
    marginBottom: 4,
  },
  lowestPrice: {
    fontSize: 28,
    fontWeight: '700',
    color: palette.text,
  },
  offersList: {
    gap: spacing.sm,
  },
  offerRow: {
    gap: spacing.md,
    paddingVertical: spacing.md,
    paddingHorizontal: spacing.xs,
    borderRadius: 0,
    backgroundColor: 'transparent',
    borderBottomWidth: 1,
    borderBottomColor: palette.border,
  },
  offerLeft: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: spacing.sm,
    flex: 1,
    minWidth: 0,
  },
  offerDot: {
    width: 12,
    height: 12,
    borderRadius: radius.pill,
    backgroundColor: palette.primary,
    marginTop: 5,
  },
  offerInfo: {
    flex: 1,
    minWidth: 0,
  },
  offerName: {
    fontSize: 15,
    color: palette.text,
    fontWeight: '700',
    marginBottom: 8,
  },
  offerPrice: {
    fontSize: 22,
    fontWeight: '700',
    color: palette.text,
  },
  offerSiteButton: {
    minHeight: 38,
    borderRadius: radius.pill,
    backgroundColor: palette.primary,
    justifyContent: 'center',
    alignItems: 'center',
  },
  offerSiteButtonText: {
    color: palette.surface,
    fontSize: 14,
    fontWeight: '700',
  },
  disabledSmallButton: {
    backgroundColor: palette.muted,
  },
  notFoundBox: {
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 80,
    gap: spacing.md,
  },
  notFoundTitle: {
    fontSize: 24,
    color: palette.text,
    fontWeight: '700',
  },
  notFoundText: {
    fontSize: 15,
    color: palette.textSoft,
    textAlign: 'center',
  },
});
