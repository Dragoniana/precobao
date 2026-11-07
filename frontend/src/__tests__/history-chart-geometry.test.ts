import {
  getHistoryAxisTicks,
  getNextGroupedPoint,
  getPointSelectionGroup,
  getTooltipPosition,
} from '../services/historyChartGeometry';

const area = { width: 320, height: 250 };
const tooltip = { width: 180, height: 92 };

function expectInside(position: { left: number; top: number }) {
  expect(position.left).toBeGreaterThanOrEqual(8);
  expect(position.top).toBeGreaterThanOrEqual(8);
  expect(position.left + tooltip.width).toBeLessThanOrEqual(area.width - 8);
  expect(position.top + tooltip.height).toBeLessThanOrEqual(area.height - 8);
}

it.each([
  ['top-left', { x: 84, y: 16 }, 'below'],
  ['top-right', { x: 284, y: 16 }, 'below'],
  ['bottom-left', { x: 84, y: 186 }, 'above'],
  ['bottom-right', { x: 284, y: 186 }, 'above'],
])('keeps a tooltip inside the chart at the %s corner', (_corner, point, verticalPlacement) => {
  const position = getTooltipPosition(point, area, tooltip);

  expectInside(position);
  if (verticalPlacement === 'below') {
    expect(position.top).toBeGreaterThan(point.y);
  } else {
    expect(position.top + tooltip.height).toBeLessThan(point.y);
  }
});

it('places a center tooltip above the point and adjusts for points close together', () => {
  const center = getTooltipPosition({ x: 160, y: 120 }, area, tooltip);
  const close = getTooltipPosition({ x: 161, y: 121 }, area, tooltip);

  expectInside(center);
  expectInside(close);
  expect(center.top + tooltip.height).toBeLessThan(120);
  expect(close.top + tooltip.height).toBeLessThan(121);
});

it('clamps a tooltip to a narrow viewport when its point is on either edge', () => {
  const narrow = { width: 250, height: 220 };
  const smallTooltip = { width: 180, height: 92 };
  const left = getTooltipPosition({ x: 40, y: 100 }, narrow, smallTooltip);
  const right = getTooltipPosition({ x: 210, y: 100 }, narrow, smallTooltip);

  expect(left.left).toBeGreaterThanOrEqual(8);
  expect(left.left + smallTooltip.width).toBeLessThanOrEqual(narrow.width - 8);
  expect(right.left).toBeGreaterThanOrEqual(8);
  expect(right.left + smallTooltip.width).toBeLessThanOrEqual(narrow.width - 8);
});

it('deduplicates same-day axis labels and centers the real date across observations', () => {
  const ticks = getHistoryAxisTicks([
    { timestamp: '2026-10-24T17:06:00Z', label: '24/10/26', x: 92 },
    { timestamp: '2026-10-24T21:44:00Z', label: '24/10/26', x: 274 },
  ], 320, 92, 64);

  expect(ticks).toHaveLength(1);
  expect(ticks[0]).toMatchObject({ label: '24/10/26', x: 183, left: 151, width: 64 });
});

it('selects a readable subset of dense date labels without collisions or clipping', () => {
  const points = Array.from({ length: 12 }, (_, index) => ({
    timestamp: `2026-10-${String(index + 1).padStart(2, '0')}T12:00:00Z`,
    label: `${String(index + 1).padStart(2, '0')}/10/26`,
    x: 92 + index * 18,
  }));
  const ticks = getHistoryAxisTicks(points, 320, 92, 64);

  expect(ticks.length).toBeLessThanOrEqual(3);
  expect(ticks[0].label).toBe('01/10/26');
  expect(ticks[ticks.length - 1].label).toBe('12/10/26');
  expect(ticks.every((tick) => tick.left >= 92 && tick.left + tick.width <= 320)).toBe(true);
  expect(ticks.slice(1).every((tick, index) => tick.left >= ticks[index].left + ticks[index].width + 8)).toBe(true);
});

it('keeps an axis label readable when the plot width cannot hold both endpoints', () => {
  const ticks = getHistoryAxisTicks([
    { timestamp: '2026-10-01T12:00:00Z', label: '01/10/26', x: 92 },
    { timestamp: '2026-10-02T12:00:00Z', label: '02/10/26', x: 130 },
  ], 150, 92, 64);

  expect(ticks).toHaveLength(1);
  expect(ticks[0].label).toBe('01/10/26');
  expect(ticks[0].left + ticks[0].width).toBeLessThanOrEqual(150);
});

it('cycles through coincident observations while preserving the original plotted coordinates', () => {
  const points = [
    { key: 'indiana', x: 180, y: 96 },
    { key: 'pacheco', x: 180, y: 96 },
    { key: 'araujo', x: 211, y: 96 },
  ];
  const group = getPointSelectionGroup(points[0], points, 22);

  expect(group.map((point) => point.key)).toEqual(['indiana', 'pacheco']);
  expect(getNextGroupedPoint('indiana', group)?.key).toBe('pacheco');
  expect(getNextGroupedPoint('pacheco', group)?.key).toBe('indiana');
  expect(group.map(({ x, y }) => [x, y])).toEqual([[180, 96], [180, 96]]);
});
