import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import {
  Animated,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';

import { palette, radius, spacing } from '../theme';

export type HistoryChartPoint = {
  timestamp: string;
  label: string;
  value: number;
};

export type HistoryChartSeries = {
  key: string;
  label: string;
  color: string;
  history: HistoryChartPoint[];
};

type HistoryChartProps = {
  series: HistoryChartSeries[];
  emptyMessage?: string;
};

type ActivePoint = {
  key: string;
  timestamp: string;
  label: string;
  value: number;
  pharmacy: string;
  color: string;
  x: number;
  y: number;
};

const CHART_HEIGHT = 250;
const AXIS_LEFT_WIDTH = 68;
const AXIS_BOTTOM_HEIGHT = 34;
const DOT_SIZE = 12;
const LINE_HEIGHT = 3;

function formatPrice(value: number) {
  return Number(value || 0).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
}

function formatCompactPrice(value: number) {
  if (value >= 1_000_000) return `R$ ${(value / 1_000_000).toFixed(1).replace('.', ',')} mi`;
  if (value >= 10_000) return `R$ ${(value / 1000).toFixed(1).replace('.', ',')}k`;
  const digits = value < 100 ? 2 : 0;
  return `R$ ${Number(value || 0).toLocaleString('pt-BR', { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

function getLineSegmentStyle(
  x1: number,
  y1: number,
  x2: number,
  y2: number,
  color: string
) {
  const dx = x2 - x1;
  const dy = y2 - y1;
  const length = Math.sqrt(dx * dx + dy * dy);
  const angle = Math.atan2(dy, dx);
  const midX = (x1 + x2) / 2;
  const midY = (y1 + y2) / 2;

  return {
    width: length,
    height: LINE_HEIGHT,
    left: midX - length / 2,
    top: midY - LINE_HEIGHT / 2,
    backgroundColor: color,
    transform: [
      {
        rotateZ: `${angle}rad`,
      },
    ],
  };
}

export function HistoryChart({ series, emptyMessage }: HistoryChartProps) {
  const isWeb = Platform.OS === 'web';

  const [chartWidth, setChartWidth] = useState(0);
  const [activePoint, setActivePoint] = useState<ActivePoint | null>(null);
  const [tooltipPoint, setTooltipPoint] = useState<ActivePoint | null>(null);

  const tooltipOpacity = useRef(new Animated.Value(0)).current;
  const tooltipScale = useRef(new Animated.Value(0.96)).current;

  const timestamps = useMemo(
    () => Array.from(new Set(series.flatMap((item) => item.history.map((point) => point.timestamp))))
      .filter((timestamp) => Number.isFinite(new Date(timestamp).getTime()))
      .sort((a, b) => new Date(a).getTime() - new Date(b).getTime()),
    [series]
  );

  const allValues = useMemo(
    () =>
      series
        .flatMap((item) => item.history.map((point) => Number(point.value)))
        .filter((value) => Number.isFinite(value)),
    [series]
  );

  const minValue = allValues.length ? Math.min(...allValues) : 0;
  const maxValue = allValues.length ? Math.max(...allValues) : 1;

  const safeMinValue =
    minValue === maxValue ? Math.max(0, minValue - 1) : minValue;

  const safeMaxValue = minValue === maxValue ? maxValue + 1 : maxValue;

  const plotWidth = Math.max(chartWidth - AXIS_LEFT_WIDTH - spacing.sm, 1);
  const plotHeight = CHART_HEIGHT - AXIS_BOTTOM_HEIGHT;

  const yTicks = useMemo(() => {
    const ticks = [];

    for (let index = 0; index <= 4; index++) {
      const percent = index / 4;
      const value = safeMaxValue - (safeMaxValue - safeMinValue) * percent;

      ticks.push({
        key: String(index),
        value,
        y: plotHeight * percent,
      });
    }

    return ticks;
  }, [safeMaxValue, safeMinValue, plotHeight]);

  const minTime = timestamps.length ? new Date(timestamps[0]).getTime() : 0;
  const maxTime = timestamps.length ? new Date(timestamps[timestamps.length - 1]).getTime() : minTime;
  const getX = useCallback((timestamp: string) => {
    if (timestamps.length <= 1 || minTime === maxTime) {
      return AXIS_LEFT_WIDTH + plotWidth / 2;
    }
    return AXIS_LEFT_WIDTH + (plotWidth * (new Date(timestamp).getTime() - minTime)) / (maxTime - minTime);
  }, [timestamps.length, minTime, maxTime, plotWidth]);

  const getY = useCallback((value: number) => {
    const range = safeMaxValue - safeMinValue;

    if (!range) {
      return plotHeight / 2;
    }

    const percent = (value - safeMinValue) / range;

    return plotHeight - percent * plotHeight;
  }, [plotHeight, safeMaxValue, safeMinValue]);

  const normalizedSeries = useMemo(
    () =>
      series.map((item) => {
        const points = [...item.history].sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime())
          .map((point) => {
            const value = Number(point.value);
            if (!Number.isFinite(value) || !Number.isFinite(new Date(point.timestamp).getTime())) {
              return null;
            }

            return {
              key: `${item.key}-${point.timestamp}`,
              timestamp: point.timestamp,
              label: point.label,
              value,
              pharmacy: item.label,
              color: item.color,
              x: getX(point.timestamp),
              y: getY(value),
            };
          })
          .filter(Boolean) as ActivePoint[];

        return {
          ...item,
          points,
        };
      }),
    [series, getX, getY]
  );

  const firstPoint =
    normalizedSeries.flatMap((item) => item.points).find(Boolean) || null;

  const visiblePoint = isWeb ? tooltipPoint : activePoint || firstPoint;

  useEffect(() => {
    if (!isWeb) {
      return;
    }

    setActivePoint(null);
    setTooltipPoint(null);
    tooltipOpacity.setValue(0);
    tooltipScale.setValue(0.96);
  }, [isWeb, series, tooltipOpacity, tooltipScale]);

  const showTooltip = (point: ActivePoint) => {
    setTooltipPoint(point);

    tooltipOpacity.stopAnimation();
    tooltipScale.stopAnimation();

    Animated.parallel([
      Animated.timing(tooltipOpacity, {
        toValue: 1,
        duration: 130,
        useNativeDriver: true,
      }),
      Animated.spring(tooltipScale, {
        toValue: 1,
        friction: 7,
        tension: 140,
        useNativeDriver: true,
      }),
    ]).start();
  };

  const hideTooltip = () => {
    tooltipOpacity.stopAnimation();
    tooltipScale.stopAnimation();

    Animated.parallel([
      Animated.timing(tooltipOpacity, {
        toValue: 0,
        duration: 240,
        useNativeDriver: true,
      }),
      Animated.timing(tooltipScale, {
        toValue: 0.96,
        duration: 240,
        useNativeDriver: true,
      }),
    ]).start(({ finished }) => {
      if (finished) {
        setTooltipPoint(null);
      }
    });
  };

  if (!series.length || !timestamps.length) {
    return (
      <View testID="history-chart-empty" style={styles.emptyCard}>
        <Text style={styles.emptyTitle}>Histórico de observações</Text>
        <Text style={styles.emptyText}>
          {emptyMessage || 'Ainda não há observações de preço registradas para este medicamento.'}
        </Text>
      </View>
    );
  }

  return (
    <View
      testID="history-chart"
      style={styles.card}
      onLayout={(event) => setChartWidth(event.nativeEvent.layout.width)}
    >
      <View style={styles.headerRow}>
        <View>
          <Text style={styles.chartTitle}>Variação de preço</Text>

          <Text style={styles.chartSubtitle}>
            Acompanhe a evolução por farmácia
          </Text>
        </View>

        <View style={styles.badge}>
          <Text style={styles.badgeText}>Linha</Text>
        </View>
      </View>

      <View style={styles.legendRow}>
        {series.map((item) => (
          <View key={item.key} style={styles.legendItem}>
            <View style={[styles.legendDot, { backgroundColor: item.color }]} />

            <Text numberOfLines={1} style={styles.legendText}>
              {item.label}
            </Text>
          </View>
        ))}
      </View>

      <View style={styles.chartOuter}>
        {chartWidth > 0 ? (
          <View style={styles.chartArea}>
            {yTicks.map((tick) => (
              <View
                key={tick.key}
                style={[
                  styles.gridLineRow,
                  {
                    top: tick.y,
                  },
                ]}
              >
                <Text style={styles.yAxisLabel}>
                  {formatCompactPrice(tick.value)}
                </Text>

                <View style={styles.gridLine} />
              </View>
            ))}

            {normalizedSeries.map((item) => (
              <View key={item.key} style={StyleSheet.absoluteFill}>
                {item.points.map((point, index) => {
                  const nextPoint = item.points[index + 1];

                  if (!nextPoint || new Date(nextPoint.timestamp).getTime() - new Date(point.timestamp).getTime() > 45 * 24 * 60 * 60 * 1000) {
                    return null;
                  }

                  return (
                    <View
                      key={`${point.key}-${nextPoint.key}`}
                      style={[
                        styles.lineSegment,
                        getLineSegmentStyle(
                          point.x,
                          point.y,
                          nextPoint.x,
                          nextPoint.y,
                          item.color
                        ),
                      ]}
                    />
                  );
                })}

                {item.points.map((point) => {
                  const isActive =
                    isWeb
                      ? activePoint?.key === point.key
                      : visiblePoint?.key === point.key;

                  return (
                    <Pressable
                      key={point.key}
                      testID="history-point"
                      accessibilityRole="button"
                      accessibilityLabel={`${point.pharmacy}, ${point.label}: ${formatPrice(point.value)}`}
                      accessibilityHint="Exibe este valor no gráfico de preços"
                      accessibilityState={{ selected: isActive }}
                      onPress={() => {
                        setActivePoint(point);
                        if (isWeb) showTooltip(point);
                      }}
                      onHoverIn={() => {
                        if (isWeb) {
                          setActivePoint(point);
                          showTooltip(point);
                        }
                      }}
                      onHoverOut={() => {
                        if (isWeb) {
                          setActivePoint(null);
                          hideTooltip();
                        }
                      }}
                      style={[
                        styles.point,
                        {
                          left: point.x - DOT_SIZE / 2,
                          top: point.y - DOT_SIZE / 2,
                          backgroundColor: point.color,
                        },
                        isActive && styles.pointActive,
                      ]}
                    />
                  );
                })}
              </View>
            ))}

            {timestamps.filter((_, index) => index === 0 || index === timestamps.length - 1 || index === Math.floor(timestamps.length / 2)).map((timestamp) => {
              const label = new Date(timestamp).toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit', year: '2-digit' });
              return (
              <Text
                key={timestamp}
                numberOfLines={1}
                style={[
                  styles.xAxisLabel,
                  {
                    left: getX(timestamp) - 24,
                    top: plotHeight + 10,
                  },
                ]}
              >
                {label}
              </Text>
              );
            })}

            {visiblePoint ? (
              <Animated.View
                pointerEvents="none"
                style={[
                  styles.tooltip,
                  {
                    left: Math.min(
                      Math.max(visiblePoint.x - 80, AXIS_LEFT_WIDTH),
                      Math.max(chartWidth - 170, AXIS_LEFT_WIDTH)
                    ),
                    top: Math.max(visiblePoint.y - 82, 8),
                  },
                  isWeb && {
                    opacity: tooltipOpacity,
                    transform: [
                      {
                        scale: tooltipScale,
                      },
                    ],
                  },
                ]}
              >
                <View style={styles.tooltipHeader}>
                  <View
                    style={[
                      styles.tooltipDot,
                      {
                        backgroundColor: visiblePoint.color,
                      },
                    ]}
                  />

                  <Text numberOfLines={1} style={styles.tooltipPharmacy}>
                    {visiblePoint.pharmacy}
                  </Text>
                </View>

                <Text style={styles.tooltipPrice}>
                  {formatPrice(visiblePoint.value)}
                </Text>

                <Text style={styles.tooltipDate}>{visiblePoint.label}</Text>
              </Animated.View>
            ) : null}
          </View>
        ) : null}
      </View>

      <Text style={styles.hintText}>
        {isWeb
          ? 'Passe o cursor ou selecione um ponto para consultar o preço.'
          : 'Toque nos pontos para alterar o preço exibido.'}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: palette.surface,
    borderRadius: radius.xl,
    borderWidth: 1,
    borderColor: palette.border,
    padding: spacing.lg,
    marginTop: spacing.sm,
    shadowColor: '#0F172A',
    shadowOpacity: 0.08,
    shadowRadius: 18,
    shadowOffset: {
      width: 0,
      height: 10,
    },
    elevation: 4,
  },

  emptyCard: {
    backgroundColor: palette.surface,
    borderRadius: radius.xl,
    borderWidth: 1,
    borderColor: palette.border,
    padding: spacing.lg,
    marginTop: spacing.sm,
    alignItems: 'center',
  },

  emptyTitle: {
    color: palette.text,
    fontSize: 18,
    fontWeight: '800',
    marginBottom: spacing.xs,
  },

  emptyText: {
    fontSize: 14,
    color: palette.textSoft,
    textAlign: 'center',
    lineHeight: 20,
  },

  headerRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    gap: spacing.md,
  },

  chartTitle: {
    fontSize: 20,
    fontWeight: '900',
    color: palette.text,
  },

  chartSubtitle: {
    marginTop: 4,
    fontSize: 13,
    color: palette.textSoft,
  },

  badge: {
    backgroundColor: palette.primarySoft,
    borderRadius: radius.pill,
    paddingHorizontal: spacing.md,
    paddingVertical: 7,
  },

  badgeText: {
    color: palette.primary,
    fontSize: 12,
    fontWeight: '800',
  },

  legendRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.sm,
    marginTop: spacing.lg,
    marginBottom: spacing.md,
  },

  legendItem: {
    maxWidth: '100%',
    flexDirection: 'row',
    alignItems: 'center',
    gap: 7,
    backgroundColor: palette.surfaceAlt,
    paddingHorizontal: 10,
    paddingVertical: 7,
    borderRadius: radius.pill,
  },

  legendDot: {
    width: 10,
    height: 10,
    borderRadius: radius.pill,
  },

  legendText: {
    maxWidth: 160,
    fontSize: 12,
    color: palette.text,
    fontWeight: '700',
  },

  chartOuter: {
    overflow: 'hidden',
    borderRadius: radius.lg,
    backgroundColor: '#F8FBFF',
    borderWidth: 1,
    borderColor: '#E7EEF8',
  },

  chartArea: {
    height: CHART_HEIGHT,
    position: 'relative',
  },

  gridLineRow: {
    position: 'absolute',
    left: 0,
    right: 0,
    height: 1,
    flexDirection: 'row',
    alignItems: 'center',
  },

  yAxisLabel: {
    width: AXIS_LEFT_WIDTH,
    paddingRight: 8,
    textAlign: 'right',
    fontSize: 10,
    color: palette.textSoft,
    fontWeight: '700',
  },

  gridLine: {
    flex: 1,
    height: 1,
    backgroundColor: '#E2EAF5',
  },

  lineSegment: {
    position: 'absolute',
    borderRadius: radius.pill,
  },

  point: {
    position: 'absolute',
    width: DOT_SIZE,
    height: DOT_SIZE,
    borderRadius: radius.pill,
    borderWidth: 3,
    borderColor: palette.surface,
    shadowColor: '#0F172A',
    shadowOpacity: 0.14,
    shadowRadius: 8,
    shadowOffset: {
      width: 0,
      height: 4,
    },
    elevation: 3,
    cursor: 'pointer' as any,
  },

  pointActive: {
    transform: [
      {
        scale: 1.35,
      },
    ],
  },

  xAxisLabel: {
    position: 'absolute',
    width: 48,
    textAlign: 'center',
    fontSize: 10,
    color: palette.textSoft,
    fontWeight: '700',
  },

  tooltip: {
    position: 'absolute',
    width: 160,
    backgroundColor: palette.surface,
    borderWidth: 1,
    borderColor: palette.border,
    borderRadius: radius.md,
    padding: spacing.sm,
    shadowColor: '#0F172A',
    shadowOpacity: 0.12,
    shadowRadius: 14,
    shadowOffset: {
      width: 0,
      height: 8,
    },
    elevation: 5,
  },

  tooltipHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 7,
  },

  tooltipDot: {
    width: 9,
    height: 9,
    borderRadius: radius.pill,
  },

  tooltipPharmacy: {
    flex: 1,
    color: palette.text,
    fontSize: 12,
    fontWeight: '800',
  },

  tooltipPrice: {
    marginTop: 6,
    color: palette.primaryDark,
    fontSize: 19,
    fontWeight: '900',
  },

  tooltipDate: {
    marginTop: 2,
    color: palette.textSoft,
    fontSize: 12,
    fontWeight: '700',
  },

  hintText: {
    marginTop: spacing.sm,
    color: palette.textSoft,
    fontSize: 12,
    lineHeight: 18,
  },
});
