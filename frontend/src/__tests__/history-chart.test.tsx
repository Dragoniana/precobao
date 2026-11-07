import React from 'react';
import { fireEvent, render } from '@testing-library/react-native';
import { StyleSheet } from 'react-native';

import { HistoryChart } from '../components/HistoryChart';
import { spacing } from '../theme';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));

const series = [{
  key: '1',
  label: 'Farmácia Central',
  color: '#1688F6',
  history: [
    { timestamp: '2026-06-10T12:00:00Z', label: '10/06/2026', value: 0.59 },
    { timestamp: '2026-06-11T12:00:00Z', label: '11/06/2026', value: 0.63 },
  ],
}];

it('lets touch and keyboard users select a chart point and reads its exact currency value', async () => {
  const { getByTestId, getByRole, getAllByTestId, getByText, getAllByText } = await render(<HistoryChart series={series} />);

  await fireEvent(getByTestId('history-chart'), 'layout', {
    nativeEvent: { layout: { x: 0, y: 0, width: 320, height: 250 } },
  });

  const secondPoint = getAllByTestId('history-point')[1];
  expect(secondPoint.props.accessibilityRole).toBe('button');
  expect(secondPoint.props.accessibilityLabel).toContain('R$ 0,63');
  await fireEvent.press(secondPoint);

  expect(getAllByText('R$ 0,63').length).toBeGreaterThan(1);
  expect(getByText('11/06/2026')).toBeTruthy();
  expect(getByRole('button', { name: /11\/06\/2026/ })).toBeTruthy();
});

it('shows monetary precision on narrow chart axes instead of rounding small prices to zero', async () => {
  const { getByTestId, getAllByText } = await render(<HistoryChart series={series} />);

  await fireEvent(getByTestId('history-chart'), 'layout', {
    nativeEvent: { layout: { x: 0, y: 0, width: 320, height: 250 } },
  });

  expect(getAllByText(/R\$ 0,5[0-9]/).length).toBeGreaterThan(0);
});

it('shows the initial observation point, date, price, and pharmacy even with one event', async () => {
  const single = [{
    ...series[0],
    history: [{ timestamp: '2026-09-01T12:00:00Z', label: '01/09/2026 09:00', value: 10.79 }],
  }];
  const { getByTestId, getAllByTestId, getAllByText } = await render(<HistoryChart series={single} />);

  await fireEvent(getByTestId('history-chart'), 'layout', {
    nativeEvent: { layout: { x: 0, y: 0, width: 360, height: 250 } },
  });

  expect(getAllByText('Farmácia Central').length).toBeGreaterThan(0);
  expect(getAllByTestId('history-point')).toHaveLength(1);
  await fireEvent.press(getAllByTestId('history-point')[0]);
  expect(getAllByText('R$\u00a010,79').length).toBeGreaterThan(0);
  expect(getAllByText('01/09/2026 09:00').length).toBeGreaterThan(0);
});

it('keeps constant observations and multiple pharmacies as separate series', async () => {
  const separateSeries = [
    { ...series[0], label: 'Indiana', history: [
      { timestamp: '2026-09-01T12:00:00Z', label: '01/09/2026', value: 10.79 },
      { timestamp: '2026-09-02T12:00:00Z', label: '02/09/2026', value: 10.79 },
    ] },
    { ...series[0], key: '2', label: 'Araujo', color: '#34A853', history: [
      { timestamp: '2026-09-01T12:00:00Z', label: '01/09/2026', value: 11.25 },
      { timestamp: '2026-09-02T12:00:00Z', label: '02/09/2026', value: 11.25 },
    ] },
  ];
  const { getByTestId, getAllByTestId, getAllByText } = await render(<HistoryChart series={separateSeries} />);

  await fireEvent(getByTestId('history-chart'), 'layout', {
    nativeEvent: { layout: { x: 0, y: 0, width: 360, height: 250 } },
  });

  expect(getAllByText('Indiana').length).toBeGreaterThan(0);
  expect(getAllByText('Araujo').length).toBeGreaterThan(0);
  expect(getAllByTestId('history-point')).toHaveLength(4);
});

it('cycles tooltip selection through points that share the same date and price without moving them', async () => {
  const coincident = [
    { ...series[0], label: 'Indiana', history: [{ timestamp: '2026-10-24T17:06:00Z', label: '24/10/2026 14:06', value: 7.05 }] },
    { ...series[0], key: '4', label: 'Pacheco', color: '#8B3FEA', history: [{ timestamp: '2026-10-24T17:06:00Z', label: '24/10/2026 14:06', value: 7.05 }] },
  ];
  const { getByTestId, getAllByTestId, getAllByText } = await render(<HistoryChart series={coincident} />);

  await fireEvent(getByTestId('history-chart'), 'layout', {
    nativeEvent: { layout: { x: 0, y: 0, width: 360, height: 280 } },
  });
  const points = getAllByTestId('history-point');
  expect(points).toHaveLength(2);
  const pointStyles = points.map((point) => StyleSheet.flatten(point.props.style));
  expect([pointStyles[0].left, pointStyles[0].top]).toEqual([pointStyles[1].left, pointStyles[1].top]);

  await fireEvent.press(points[1]);
  expect(getAllByText('Pacheco').length).toBeGreaterThan(1);
  await fireEvent.press(getAllByTestId('history-point')[1]);
  expect(getAllByText('Indiana').length).toBeGreaterThan(1);
});

it('keeps the minimum and maximum price markers inside the chart bounds', async () => {
  const extremes = [
    { ...series[0], history: [series[0].history[0]] },
    {
      ...series[0],
      key: '2',
      label: 'Pacheco',
      color: '#34A853',
      history: [{
        timestamp: '2026-06-11T12:00:00Z',
        label: '11/06/2026',
        value: 12,
      }],
    },
  ];
  const { getByTestId, getAllByTestId } = await render(<HistoryChart series={extremes} />);

  await fireEvent(getByTestId('history-chart'), 'layout', {
    nativeEvent: { layout: { x: 0, y: 0, width: 360, height: 250 } },
  });

  const markerStyles = getAllByTestId('history-point').map((point) =>
    StyleSheet.flatten(point.props.style),
  );
  expect(Math.min(...markerStyles.map((style) => style.top))).toBeGreaterThanOrEqual(0);
  expect(Math.max(...markerStyles.map((style) => style.top + style.height))).toBeLessThanOrEqual(280 - 8);
  expect(Math.max(...markerStyles.map((style) => style.left + style.width))).toBeLessThanOrEqual(
    360 - spacing.lg * 2 - 4,
  );
  const axisLabelStyles = getAllByTestId('history-x-axis-label').map((label) =>
    StyleSheet.flatten(label.props.style),
  );
  expect(Math.max(...axisLabelStyles.map((style) => style.left + style.width))).toBeLessThanOrEqual(
    360 - spacing.lg * 2 - 4,
  );
});

it('shows an explicit empty state when there are no observations', async () => {
  const { getByTestId, getByText } = await render(
    <HistoryChart series={[]} emptyMessage="Ainda não existem observações reais." />
  );

  expect(getByTestId('history-chart-empty')).toBeTruthy();
  expect(getByText('Ainda não existem observações reais.')).toBeTruthy();
});
