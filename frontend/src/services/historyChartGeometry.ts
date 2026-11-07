export type ChartPointPosition = { x: number; y: number };
export type ChartAreaSize = { width: number; height: number };
export type TooltipPosition = { left: number; top: number };
export type HistoryAxisPoint = { timestamp: string; label: string; x: number };
export type HistoryAxisTick = { timestamp: string; label: string; x: number; left: number; width: number };
export type PositionedChartPoint = ChartPointPosition & { key: string };

const EDGE_GAP = 8;
const POINT_GAP = 12;
const AXIS_LABEL_GAP = 8;

function clamp(value: number, minimum: number, maximum: number) {
  return Math.min(Math.max(value, minimum), Math.max(minimum, maximum));
}

export function getTooltipPosition(
  point: ChartPointPosition,
  area: ChartAreaSize,
  tooltip: ChartAreaSize,
): TooltipPosition {
  const leftSpace = point.x - EDGE_GAP;
  const rightSpace = area.width - point.x - EDGE_GAP;
  let left = point.x - tooltip.width / 2;

  if (rightSpace < tooltip.width / 2 && leftSpace >= tooltip.width + POINT_GAP) {
    left = point.x - tooltip.width - POINT_GAP;
  } else if (leftSpace < tooltip.width / 2 && rightSpace >= tooltip.width + POINT_GAP) {
    left = point.x + POINT_GAP;
  }

  const topSpace = point.y - EDGE_GAP;
  const bottomSpace = area.height - point.y - EDGE_GAP;
  let top = point.y - tooltip.height - POINT_GAP;

  if (topSpace < tooltip.height + POINT_GAP && bottomSpace >= tooltip.height + POINT_GAP) {
    top = point.y + POINT_GAP;
  } else if (topSpace < tooltip.height + POINT_GAP) {
    top = point.y - tooltip.height / 2;
  }

  return {
    left: clamp(left, EDGE_GAP, area.width - tooltip.width - EDGE_GAP),
    top: clamp(top, EDGE_GAP, area.height - tooltip.height - EDGE_GAP),
  };
}

/** Groups observations on the same displayed calendar date and chooses non-colliding axis labels. */
export function getHistoryAxisTicks(
  points: HistoryAxisPoint[],
  chartWidth: number,
  plotLeft: number,
  requestedLabelWidth: number,
  gap = AXIS_LABEL_GAP,
): HistoryAxisTick[] {
  const groups: { timestamp: string; label: string; xTotal: number; count: number }[] = [];
  for (const point of points) {
    if (!Number.isFinite(point.x) || !point.label) continue;
    const current = groups[groups.length - 1];
    if (current?.label === point.label) {
      current.xTotal += point.x;
      current.count += 1;
    } else {
      groups.push({ timestamp: point.timestamp, label: point.label, xTotal: point.x, count: 1 });
    }
  }

  if (!groups.length || chartWidth <= plotLeft) return [];

  const availableWidth = chartWidth - plotLeft;
  const labelWidth = Math.min(Math.max(1, requestedLabelWidth), availableWidth);
  const maxLabels = Math.max(1, Math.floor((availableWidth + gap) / (labelWidth + gap)));
  const count = Math.min(groups.length, maxLabels);
  const selectedIndexes = count === 1
    ? [Math.floor((groups.length - 1) / 2)]
    : Array.from({ length: count }, (_, index) => Math.round(index * (groups.length - 1) / (count - 1)));

  const selected = selectedIndexes.map((index) => {
    const group = groups[index];
    return {
      ...group,
      x: group.xTotal / group.count,
      endpoint: groups.length > 1 && (index === 0 || index === groups.length - 1),
    };
  });
  const rightmost = chartWidth - labelWidth;
  let previousRight = plotLeft - gap;

  return selected.flatMap((item, index) => {
    const isFirst = index === 0;
    const isLast = index === selected.length - 1;
    const centeredLeft = item.x - labelWidth / 2;
    const left = isFirst && item.endpoint
      ? plotLeft
      : isLast && item.endpoint
        ? rightmost
        : clamp(centeredLeft, plotLeft, rightmost);

    if (left < previousRight + gap || (!isLast && left + labelWidth + gap > rightmost)) return [];
    previousRight = left + labelWidth;
    return [{ timestamp: item.timestamp, label: item.label, x: item.x, left, width: labelWidth }];
  });
}

/** Returns other markers within one comfortable tap radius without moving their plotted values. */
export function getPointSelectionGroup<T extends PositionedChartPoint>(
  point: T,
  points: T[],
  radius: number,
): T[] {
  const radiusSquared = radius * radius;
  return points.filter((candidate) => {
    const dx = candidate.x - point.x;
    const dy = candidate.y - point.y;
    return dx * dx + dy * dy <= radiusSquared;
  });
}

export function getNextGroupedPoint<T extends { key: string }>(currentKey: string | undefined, group: T[]): T | undefined {
  if (!group.length) return undefined;
  const currentIndex = group.findIndex((point) => point.key === currentKey);
  return group[(currentIndex + 1 + group.length) % group.length];
}
