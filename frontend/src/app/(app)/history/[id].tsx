import { Ionicons } from '@expo/vector-icons';
import { useLocalSearchParams, useRouter } from 'expo-router';
import React, { useEffect, useMemo, useState } from 'react';

import {
  ActivityIndicator,
  Linking,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  useWindowDimensions,
  View,
} from 'react-native';

import {
  HistoryChart,
  HistoryChartSeries,
} from '../../../components/HistoryChart';

import { OfferRow } from '../../../components/OfferRow';
import { Screen } from '../../../components/Screen';
import { SearchBar } from '../../../components/SearchBar';
import { useCatalogContext, useSessionContext } from '../../../context/AppContext';

import {
  Medicamento,
  Oferta,
} from '../../../types/api';
import { api } from '../../../services/api';

import { palette, radius, spacing } from '../../../theme';

const PHARMACY_COLORS = [
  '#1688F6',
  '#34A853',
  '#F2994A',
  '#9B51E0',
  '#EB5757',
  '#2D9CDB',
  '#27AE60',
  '#BB6BD9',
];

function formatPrice(value?: number) {
  if (value === undefined || !Number.isFinite(value)) {
    return 'Sem preço';
  }

  return `R$ ${value.toFixed(2).replace('.', ',')}`;
}

function formatDateLabel(value: string) {
  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return 'Atual';
  }

  return date.toLocaleDateString('pt-BR', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function getPharmacyName(offer?: Oferta) {
  if (!offer) {
    return 'Farmácia';
  }

  return (
    offer.farmacia_nome ||
    offer.farmacia?.nome_fantasia ||
    offer.farmacia?.razao_social ||
    `Farmácia ${offer.farmacia_id}`
  );
}

function getColorByIndex(index: number) {
  return PHARMACY_COLORS[index % PHARMACY_COLORS.length];
}

function getLowestOffer(offers: Oferta[]) {
  if (!offers.length) {
    return undefined;
  }

  return [...offers].sort((a, b) => Number(a.preco) - Number(b.preco))[0];
}

const HISTORY_PERIODS = [
  { value: '7d', label: '7 dias' }, { value: '30d', label: '30 dias' },
  { value: '90d', label: '90 dias' }, { value: '6m', label: '6 meses' },
  { value: '1y', label: '1 ano' }, { value: 'all', label: 'Tudo' },
] as const;

export default function HistoryScreen() {
  const router = useRouter();

  const handleBack = () => {
  if (router.canGoBack()) {
    router.back();
    return;
  }

  router.replace('/home');
};

  const params = useLocalSearchParams<{
    id?: string | string[];
  }>();

  const { getMedicineById, loadMedicineById } = useCatalogContext();
  const { isLoading } = useSessionContext();

  const [search, setSearch] = useState('Preço Bão');
  const [series, setSeries] = useState<HistoryChartSeries[]>([]);
  const [selectedPharmacies, setSelectedPharmacies] = useState<number[] | null>(null);
  const [historyStats, setHistoryStats] = useState<{ key: string; label: string; minimum: number; maximum: number; average: number; variation: number | null }[]>([]);
  const [totalHistoryObservations, setTotalHistoryObservations] = useState(0);
  const [isLoadingHistory, setIsLoadingHistory] = useState(true);
  const [historyMessage, setHistoryMessage] = useState('');
  const [loadedMedicine, setLoadedMedicine] = useState<Medicamento | undefined>();
  const [medicineLoading, setMedicineLoading] = useState(true);
  const [period, setPeriod] = useState<(typeof HISTORY_PERIODS)[number]['value']>('all');

  const { width } = useWindowDimensions();

  const isWeb = Platform.OS === 'web';
  const isDesktop = isWeb && width >= 1100;
  const isTablet = isWeb && width >= 800 && width < 1100;

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
      setMedicineLoading(false);
      return;
    }
    setLoadedMedicine(undefined);
    setHistoryMessage('');
    setMedicineLoading(true);
    loadMedicineById(medicineId).then((item) => {
      if (active) setLoadedMedicine(item);
    }).catch(() => {
      if (active) setHistoryMessage('Não foi possível carregar o medicamento.');
    }).finally(() => {
      if (active) setMedicineLoading(false);
    });
    return () => { active = false; };
  }, [loadMedicineById, medicineId]);

  const currentOffers = useMemo(() => {
    if (!medicine?.ofertas?.length) {
      return [];
    }

    return [...medicine.ofertas].sort((a, b) => Number(a.preco) - Number(b.preco));
  }, [medicine]);

  const lowestOffer = useMemo(() => getLowestOffer(currentOffers), [currentOffers]);
  const visibleSeries = useMemo(() => selectedPharmacies === null ? series : series.filter((item) => selectedPharmacies.includes(Number(item.key))), [selectedPharmacies, series]);
  const chartEmptyMessage = historyMessage
    ? 'Não foi possível consultar o histórico agora. Tente novamente mais tarde.'
    : selectedPharmacies !== null && series.length > 0 && visibleSeries.length === 0
    ? 'Selecione ao menos uma farmácia para visualizar as observações.'
    : series.length === 0 && totalHistoryObservations > 0 && period !== 'all'
      ? `Não há observações neste período. Existem ${totalHistoryObservations} observações em outras datas; selecione Tudo para consultá-las.`
      : 'O acompanhamento ainda não possui observações reais de preço. Uma coleta bem-sucedida registrará o primeiro ponto.';

  const summaryCardWidth = isDesktop ? '31.5%' : isTablet ? '48%' : '100%';

  useEffect(() => {
    let active = true;
    const fetchHistory = async () => {
      if (!medicine || medicineId === null) {
        setSeries([]);
        setHistoryStats([]);
        setTotalHistoryObservations(0);
        setIsLoadingHistory(false);
        return;
      }

      setIsLoadingHistory(true);
      setHistoryMessage('');

      try {
        const response = await api.get(`/api/v1/medicines/${medicineId}/history?periodo=${period}`);
        const data = response.data as { pharmacies?: { farmacia_id: number; pharmacy_name?: string; history: { data_registro: string; preco: number }[] }[]; statistics?: Record<string, { menor_preco: number; maior_preco: number; preco_medio: number; variacao_percentual: number | null }>; total_observacoes?: number };
        const normalized = (data.pharmacies || []).map((item, index) => ({
          key: String(item.farmacia_id),
          label: item.pharmacy_name || `Farm\u00e1cia ${item.farmacia_id}`,
          color: getColorByIndex(index),
          history: item.history.map((point) => ({
            timestamp: point.data_registro,
            label: formatDateLabel(point.data_registro),
            value: Number(point.preco),
          })),
        }));
        if (active) {
          setSeries(normalized);
          setTotalHistoryObservations(Number(data.total_observacoes ?? normalized.reduce((count, item) => count + item.history.length, 0)));
          setSelectedPharmacies(null);
          setHistoryStats(normalized.flatMap((item) => {
            const stat = data.statistics?.[item.key];
            return stat ? [{ key: item.key, label: item.label, minimum: Number(stat.menor_preco), maximum: Number(stat.maior_preco), average: Number(stat.preco_medio), variation: stat.variacao_percentual === null ? null : Number(stat.variacao_percentual) }] : [];
          }));
          setHistoryMessage('');
        }
      } catch {
        if (active) {
          setSeries([]);
          setHistoryStats([]);
          setTotalHistoryObservations(0);
          setHistoryMessage('N\u00e3o foi poss\u00edvel carregar o hist\u00f3rico real agora. As ofertas atuais continuam dispon\u00edveis abaixo.');
        }
      } finally {
        if (active) setIsLoadingHistory(false);
      }
    };

    void fetchHistory();
    return () => { active = false; };
  }, [medicine, medicineId, period]);

  if (isLoading || medicineLoading) {
    return (
      <Screen contentStyle={styles.centerContent}>
        <ActivityIndicator size="large" color={palette.primary} />

        <Text style={styles.feedbackText}>Carregando catálogo...</Text>
      </Screen>
    );
  }

  if (!medicine) {
    return (
      <Screen>
        <SearchBar
          value={search}
          onChangeText={setSearch}
          onSubmit={() =>
            router.push({
              pathname: '/search',
              params: {
                q: search,
              },
            })
          }
          onBack={handleBack}
        />

        <View style={styles.notFoundBox}>
          <View style={styles.notFoundIcon}>
            <Ionicons name="alert-circle-outline" size={36} color={palette.primary} />
          </View>

          <Text style={styles.notFoundTitle}>Histórico não encontrado</Text>

          <Text style={styles.notFoundText}>
            Não foi possível localizar esse medicamento no catálogo carregado.
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
        onSubmit={() =>
          router.push({
            pathname: '/search',
            params: {
              q: search,
            },
          })
        }
        onBack={handleBack}
      />

      <View style={[styles.heroCard, isDesktop && styles.heroCardDesktop]}>
        <View style={styles.heroTextBox}>
          <View style={styles.heroBadge}>
            <Ionicons name="analytics-outline" size={16} color={palette.primary} />

            <Text style={styles.heroBadgeText}>Histórico de preços</Text>
          </View>

          <Text style={[styles.title, isDesktop && styles.titleDesktop]}>
            {medicine.nome}
          </Text>

          <Text style={styles.subtitle}>
            Veja a evolução dos preços por farmácia e compare com as ofertas atuais.
          </Text>
        </View>

        <View style={styles.heroPriceBox}>
          <Text style={styles.heroPriceLabel}>Menor preço atual</Text>

          <Text style={styles.heroPrice}>
            {lowestOffer ? formatPrice(Number(lowestOffer.preco)) : 'Sem preço'}
          </Text>

          <Text numberOfLines={1} style={styles.heroPharmacy}>
            {lowestOffer ? getPharmacyName(lowestOffer) : 'Nenhuma farmácia disponível'}
          </Text>
        </View>
      </View>

      {currentOffers.length > 1 && (
        <View style={styles.summaryGrid}>
          {currentOffers.slice(0, 3).map((offer, index) => (
            <View
              key={offer.id || `${offer.farmacia_id}-${index}`}
              style={[
                styles.summaryCard,
                {
                  width: summaryCardWidth,
                },
              ]}
            >
              <View style={styles.summaryTopRow}>
                <View
                  style={[
                    styles.summaryDot,
                    {
                      backgroundColor: getColorByIndex(index),
                    },
                  ]}
                />

                <Text numberOfLines={1} style={styles.summaryPharmacy}>
                  {getPharmacyName(offer)}
                </Text>
              </View>

              <Text style={styles.summaryPrice}>
                {formatPrice(Number(offer.preco))}
              </Text>

              <View
                style={[
                  styles.stockBadge,
                  offer.disponivel ? styles.stockAvailable : styles.stockUnavailable,
                ]}
              >
                <Text
                  style={[
                    styles.stockBadgeText,
                    offer.disponivel
                      ? styles.stockAvailableText
                      : styles.stockUnavailableText,
                  ]}
                >
                  {offer.disponivel ? 'Disponível' : 'Indisponível'}
                </Text>
              </View>
            </View>
          ))}
        </View>
      )}

      <View style={styles.sectionHeader}>
        <View>
          <Text style={styles.sectionTitle}>Evolução dos preços</Text>

          <Text style={styles.sectionSubtitle}>
            Compare as variações de preço registradas para este medicamento.
          </Text>
        </View>
      </View>

      <View style={styles.periodFilters} accessibilityRole="tablist">
        {HISTORY_PERIODS.map((option) => (
          <Pressable
            key={option.value}
            accessibilityRole="tab"
            accessibilityState={{ selected: period === option.value }}
            onPress={() => setPeriod(option.value)}
            style={[styles.periodChip, period === option.value && styles.periodChipSelected]}
          >
            <Text style={[styles.periodChipText, period === option.value && styles.periodChipTextSelected]}>
              {option.label}
            </Text>
          </Pressable>
        ))}
      </View>

      {!!series.length && (
        <View style={styles.periodFilters} accessibilityLabel="Filtrar farmácias">
          <Pressable
            accessibilityRole="button"
            accessibilityState={{ selected: selectedPharmacies === null }}
            onPress={() => setSelectedPharmacies(null)}
            style={[styles.periodChip, selectedPharmacies === null && styles.periodChipSelected]}
          >
            <Text style={[styles.periodChipText, selectedPharmacies === null && styles.periodChipTextSelected]}>Todas</Text>
          </Pressable>
          {series.map((item) => {
            const id = Number(item.key);
            const selected = selectedPharmacies === null || selectedPharmacies.includes(id);
            return (
              <Pressable key={item.key} accessibilityRole="button" accessibilityState={{ selected }} onPress={() => setSelectedPharmacies((current) => current === null ? series.filter((candidate) => Number(candidate.key) !== id).map((candidate) => Number(candidate.key)) : current.includes(id) ? current.filter((value) => value !== id) : [...current, id])} style={[styles.periodChip, selected && { borderColor: item.color }]}>
                <Text style={[styles.periodChipText, selected && { color: item.color }]}>{item.label}</Text>
              </Pressable>
            );
          })}
        </View>
      )}

      {historyMessage ? (
        <View style={styles.messageBox}>
          <Ionicons name="information-circle-outline" size={20} color={palette.warning} />

          <Text style={styles.messageText}>{historyMessage}</Text>
        </View>
      ) : null}

      {isLoadingHistory ? (
        <View style={styles.loadingHistoryBox}>
          <ActivityIndicator size="small" color={palette.primary} />

          <Text style={styles.loadingHistoryText}>Carregando histórico...</Text>
        </View>
      ) : (
        <HistoryChart series={visibleSeries} emptyMessage={chartEmptyMessage} />
      )}

      {!!historyStats.length && (
        <View style={styles.summaryGrid}>
          {historyStats.map((stat) => (
            <View key={stat.key} style={[styles.summaryCard, { width: summaryCardWidth }]}>
              <Text numberOfLines={1} style={styles.summaryPharmacy}>{stat.label}</Text>
              <Text style={styles.summaryPrice}>Mín. {formatPrice(stat.minimum)}</Text>
              <Text style={styles.sectionSubtitle}>Máx. {formatPrice(stat.maximum)} · média {formatPrice(stat.average)}</Text>
              <Text style={styles.sectionSubtitle}>Variação {stat.variation === null ? 'indisponível' : `${stat.variation.toFixed(2).replace('.', ',')}%`}</Text>
            </View>
          ))}
        </View>
      )}

      {!!currentOffers.length && (
        <>
          <View style={styles.sectionHeader}>
            <View>
              <Text style={styles.sectionTitle}>Ofertas atuais</Text>

              <Text style={styles.sectionSubtitle}>
                Lista de preços disponíveis neste momento.
              </Text>
            </View>
          </View>

          <View style={styles.offersBox}>
            {currentOffers.map((offer, index) => (
              <OfferRow
                key={offer.id || `${offer.farmacia_id}-${index}`}
                medicine={medicine}
                medicineName={medicine.nome}
                pharmacy={getPharmacyName(offer)}
                price={offer.preco}
                color={getColorByIndex(index)}
                onPress={() => {
                  if (offer.url_origem?.startsWith('https://')) {
                    void Linking.openURL(offer.url_origem).catch(() => {
                      setHistoryMessage('Não foi possível abrir a oferta desta farmácia.');
                    });
                  }
                }}
              />
            ))}
          </View>
        </>
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  centerContent: {
    justifyContent: 'center',
    alignItems: 'center',
    flex: 1,
    paddingTop: spacing.xl * 2,
  },

  heroCard: {
    marginTop: spacing.lg,
    backgroundColor: palette.surface,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: palette.border,
    padding: spacing.lg,
    gap: spacing.lg,
    shadowColor: '#0F172A',
    shadowOpacity: 0,
    shadowRadius: 0,
    shadowOffset: {
      width: 0,
      height: 10,
    },
    elevation: 0,
  },

  heroCardDesktop: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: spacing.xl,
  },

  heroTextBox: {
    flex: 1,
  },

  heroBadge: {
    alignSelf: 'flex-start',
    flexDirection: 'row',
    alignItems: 'center',
    gap: 7,
    backgroundColor: palette.primarySoft,
    borderRadius: radius.pill,
    paddingHorizontal: spacing.md,
    paddingVertical: 8,
    marginBottom: spacing.md,
  },

  heroBadgeText: {
    color: palette.primary,
    fontSize: 12,
    fontWeight: '900',
  },

  title: {
    fontSize: 24,
    lineHeight: 31,
    fontWeight: '700',
    color: palette.text,
  },

  titleDesktop: {
    fontSize: 32,
    lineHeight: 39,
  },

  subtitle: {
    marginTop: spacing.sm,
    fontSize: 15,
    lineHeight: 22,
    color: palette.textSoft,
    maxWidth: 660,
  },

  heroPriceBox: {
    minWidth: 220,
    paddingTop: spacing.sm,
  },

  heroPriceLabel: {
    color: palette.textSoft,
    fontSize: 13,
    fontWeight: '600',
  },

  heroPrice: {
    marginTop: 6,
    color: palette.primaryDark,
    fontSize: 28,
    fontWeight: '700',
  },

  heroPharmacy: {
    marginTop: 4,
    color: palette.text,
    fontSize: 14,
    fontWeight: '700',
  },

  summaryGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.md,
    marginTop: spacing.md,
  },
  periodFilters: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.sm,
    marginBottom: spacing.md,
  },
  periodChip: {
    minHeight: 40,
    justifyContent: 'center',
    paddingHorizontal: spacing.md,
    borderRadius: radius.pill,
    borderWidth: 1,
    borderColor: palette.border,
    backgroundColor: palette.surface,
  },
  periodChipSelected: { backgroundColor: palette.primary, borderColor: palette.primary },
  periodChipText: { color: palette.textSoft, fontWeight: '600' },
  periodChipTextSelected: { color: '#FFFFFF' },

  summaryCard: {
    backgroundColor: palette.surface,
    borderWidth: 1,
    borderColor: palette.border,
    borderRadius: radius.xl,
    padding: spacing.lg,
    shadowColor: '#0F172A',
    shadowOpacity: 0.06,
    shadowRadius: 14,
    shadowOffset: {
      width: 0,
      height: 8,
    },
    elevation: 3,
  },

  summaryTopRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginBottom: 10,
  },

  summaryDot: {
    width: 12,
    height: 12,
    borderRadius: radius.pill,
  },

  summaryPharmacy: {
    flex: 1,
    fontSize: 14,
    fontWeight: '800',
    color: palette.text,
  },

  summaryPrice: {
    fontSize: 25,
    fontWeight: '900',
    color: palette.text,
    marginBottom: spacing.sm,
  },

  stockBadge: {
    alignSelf: 'flex-start',
    borderRadius: radius.pill,
    paddingHorizontal: spacing.md,
    paddingVertical: 7,
  },

  stockAvailable: {
    backgroundColor: '#EAF8EF',
  },

  stockUnavailable: {
    backgroundColor: '#FFF1F1',
  },

  stockBadgeText: {
    fontSize: 12,
    fontWeight: '900',
  },

  stockAvailableText: {
    color: palette.success,
  },

  stockUnavailableText: {
    color: palette.danger,
  },

  sectionHeader: {
    marginTop: spacing.xl,
    marginBottom: spacing.sm,
  },

  sectionTitle: {
    fontSize: 24,
    color: palette.text,
    fontWeight: '900',
  },

  sectionSubtitle: {
    marginTop: 4,
    color: palette.textSoft,
    fontSize: 14,
    lineHeight: 20,
  },

  offersBox: {
    backgroundColor: palette.surface,
    borderRadius: radius.xl,
    borderWidth: 1,
    borderColor: palette.border,
    paddingHorizontal: spacing.md,
    marginTop: spacing.sm,
    overflow: 'hidden',
  },

  messageBox: {
    marginTop: spacing.sm,
    marginBottom: spacing.sm,
    backgroundColor: '#FFF8E8',
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: '#F5D48A',
    padding: spacing.md,
    flexDirection: 'row',
    gap: spacing.sm,
    alignItems: 'flex-start',
  },

  messageText: {
    flex: 1,
    color: palette.text,
    fontSize: 14,
    lineHeight: 20,
  },

  loadingHistoryBox: {
    marginTop: spacing.sm,
    backgroundColor: palette.surface,
    borderRadius: radius.xl,
    borderWidth: 1,
    borderColor: palette.border,
    padding: spacing.xl,
    alignItems: 'center',
    justifyContent: 'center',
    gap: spacing.sm,
  },

  loadingHistoryText: {
    fontSize: 15,
    color: palette.textSoft,
    fontWeight: '700',
  },

  feedbackText: {
    marginTop: spacing.lg,
    fontSize: 16,
    color: palette.textSoft,
  },

  notFoundBox: {
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 80,
    gap: spacing.md,
  },

  notFoundIcon: {
    width: 70,
    height: 70,
    borderRadius: radius.xl,
    backgroundColor: palette.primarySoft,
    justifyContent: 'center',
    alignItems: 'center',
  },

  notFoundTitle: {
    fontSize: 24,
    color: palette.text,
    fontWeight: '900',
  },

  notFoundText: {
    fontSize: 15,
    color: palette.textSoft,
    textAlign: 'center',
    lineHeight: 22,
    maxWidth: 420,
  },
});
