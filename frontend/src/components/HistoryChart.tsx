import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import {
  Animated,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  useWindowDimensions,
  View,
} from 'react-native';

import { radius, spacing } from '../theme';
import { createThemedStyles } from '../context/ThemeContext';
import {
  getHistoryAxisTicks,
  getNextGroupedPoint,
  getPointSelectionGroup,
  getTooltipPosition,
} from '../services/historyChartGeometry';

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

const BASE_CHART_HEIGHT = 280;
const MAX_CHART_HEIGHT = 320;
const AXIS_LEFT_WIDTH = 68;
const AXIS_BOTTOM_HEIGHT = 48;
const DOT_SIZE = 12;
const POINT_TARGET_SIZE = 44;
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
  const styles = useStyles();
  const isWeb = Platform.OS === 'web';
  const { fontScale } = useWindowDimensions();
  const chartHeight = Math.min(
    MAX_CHART_HEIGHT,
    Math.max(BASE_CHART_HEIGHT, Math.ceil(BASE_CHART_HEIGHT * fontScale)),
  );

  const [chartWidth, setChartWidth] = useState(0);
  const [chartAreaSize, setChartAreaSize] = useState({ width: 0, height: chartHeight });
  const [tooltipSize, setTooltipSize] = useState({ width: 180, height: 92 });
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

  const pointRadius = POINT_TARGET_SIZE / 2;
  const plotLeft = AXIS_LEFT_WIDTH + pointRadius + 2;
  const plotWidth = Math.max(
    chartWidth - plotLeft - spacing.md - pointRadius,
    1,
  );
  const plotTop = pointRadius;
  const plotHeight = chartHeight - AXIS_BOTTOM_HEIGHT - plotTop - pointRadius;

  const yTicks = useMemo(() => {
    const ticks = [];

    for (let index = 0; index <= 4; index++) {
      const percent = index / 4;
      const value = safeMaxValue - (safeMaxValue - safeMinValue) * percent;

      ticks.push({
        key: String(index),
        value,
        y: plotTop + plotHeight * percent,
      });
    }

    return ticks;
  }, [plotHeight, plotTop, safeMaxValue, safeMinValue]);

  const minTime = timestamps.length ? new Date(timestamps[0]).getTime() : 0;
  const maxTime = timestamps.length ? new Date(timestamps[timestamps.length - 1]).getTime() : minTime;
  const getX = useCallback((timestamp: string) => {
    if (timestamps.length <= 1 || minTime === maxTime) {
      return plotLeft + plotWidth / 2;
    }
    return plotLeft + (plotWidth * (new Date(timestamp).getTime() - minTime)) / (maxTime - minTime);
  }, [timestamps.length, minTime, maxTime, plotLeft, plotWidth]);

  const getY = useCallback((value: number) => {
    const range = safeMaxValue - safeMinValue;

    if (!range) {
      return plotHeight / 2;
    }

    const percent = (value - safeMinValue) / range;

    return plotTop + plotHeight - percent * plotHeight;
  }, [plotHeight, plotTop, safeMaxValue, safeMinValue]);

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
  const chartPoints = normalizedSeries.flatMap((item) => item.points);

  const visiblePoint = isWeb ? tooltipPoint : activePoint || firstPoint;
  const tooltipPosition = visiblePoint
    ? getTooltipPosition(visiblePoint, chartAreaSize, tooltipSize)
    : { left: 0, top: 0 };

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

  const selectPoint = (point: ActivePoint) => {
    const group = getPointSelectionGroup(point, chartPoints, POINT_TARGET_SIZE / 2);
    const selected = group.length > 1
      ? getNextGroupedPoint(visiblePoint?.key, group) ?? point
      : point;
    setActivePoint(selected);
    if (isWeb) showTooltip(selected);
  };

  const axisTicks = getHistoryAxisTicks(
    timestamps.map((timestamp) => ({
      timestamp,
      label: new Date(timestamp).toLocaleDateString('pt-BR', {
        day: '2-digit', month: '2-digit', year: '2-digit',
      }),
      x: getX(timestamp),
    })),
    chartWidth,
    plotLeft,
    64 * Math.max(1, fontScale),
  );

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
      onLayout={(event) =>
        setChartWidth(Math.max(0, event.nativeEvent.layout.width - spacing.lg * 2 - 4))
      }
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
          <View
            testID="history-chart-area"
            style={[styles.chartArea, { height: chartHeight }]}
            onLayout={(event) => {
              const { width, height } = event.nativeEvent.layout;
              setChartWidth(width);
              setChartAreaSize({ width, height });
            }}
          >
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
              <View key={item.key} pointerEvents="box-none" style={StyleSheet.absoluteFill}>
                {item.points.map((point, index) => {
                  const nextPoint = item.points[index + 1];

                  if (!nextPoint || new Date(nextPoint.timestamp).getTime() - new Date(point.timestamp).getTime() > 45 * 24 * 60 * 60 * 1000) {
                    return null;
                  }

                  return (
                    <View
                      key={`${point.key}-${nextPoint.key}`}
                      pointerEvents="none"
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
                      hitSlop={6}
                      onPress={() => selectPoint(point)}
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
                        styles.pointTarget,
                        {
                          left: point.x - POINT_TARGET_SIZE / 2,
                          top: point.y - POINT_TARGET_SIZE / 2,
                        },
                        isActive && styles.pointActive,
                      ]}
                    >
                      <View style={[styles.point, { backgroundColor: point.color }]} />
                    </Pressable>
                  );
                })}
              </View>
            ))}

            {axisTicks.map((tick) => {
              return (
              <Text
                key={`${tick.timestamp}-${tick.label}`}
                testID="history-x-axis-label"
                numberOfLines={1}
                style={[
                  styles.xAxisLabel,
                  {
                    left: tick.left,
                    width: tick.width,
                    top: plotTop + plotHeight + pointRadius + 4,
                  },
                ]}
                pointerEvents="none"
              >
                {tick.label}
              </Text>
              );
            })}

            {visiblePoint ? (
              <Animated.View
                testID="history-tooltip"
                pointerEvents="none"
                style={[
                  styles.tooltip,
                  {
                    left: tooltipPosition.left,
                    top: tooltipPosition.top,
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
                onLayout={(event) => {
                  const { width, height } = event.nativeEvent.layout;
                  setTooltipSize((current) =>
                    current.width === width && current.height === height
                      ? current
                      : { width, height },
                  );
                }}
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

const useStyles = createThemedStyles((palette) => ({
  card: {
    backgroundColor: palette.surface,
    borderRadius: radius.xl,
    borderWidth: 1,
    borderColor: palette.border,
    padding: spacing.lg,
    marginTop: spacing.sm,
    shadowColor: palette.shadow,
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
    backgroundColor: palette.chartBackground,
    borderWidth: 1,
    borderColor: palette.border,
  },

  chartArea: {
    height: BASE_CHART_HEIGHT,
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
    backgroundColor: palette.chartGrid,
  },

  lineSegment: {
    position: 'absolute',
    borderRadius: radius.pill,
  },

  point: {
    width: DOT_SIZE,
    height: DOT_SIZE,
    borderRadius: radius.pill,
    borderWidth: 3,
    borderColor: palette.surface,
    shadowColor: palette.shadow,
    shadowOpacity: 0.14,
    shadowRadius: 8,
    shadowOffset: {
      width: 0,
      height: 4,
    },
    elevation: 3,
    cursor: 'pointer' as any,
  },

  pointTarget: {
    position: 'absolute',
    width: POINT_TARGET_SIZE,
    height: POINT_TARGET_SIZE,
    borderRadius: radius.pill,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: 'transparent',
    borderWidth: 0,
    cursor: 'pointer' as any,
    zIndex: 2,
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
    textAlign: 'center',
    fontSize: 10,
    color: palette.textSoft,
    fontWeight: '700',
    zIndex: 1,
  },

  tooltip: {
    position: 'absolute',
    width: 180,
    zIndex: 10,
    backgroundColor: palette.surface,
    borderWidth: 1,
    borderColor: palette.border,
    borderRadius: radius.md,
    padding: spacing.sm,
    shadowColor: palette.shadow,
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
}));
