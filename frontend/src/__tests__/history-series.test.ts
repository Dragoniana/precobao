import {
  getHistoryPharmacyOptions,
  getVisibleHistorySeries,
  toggleHistoryPharmacy,
} from '../services/historySeries';

const series = [
  { key: '1', label: 'Indiana' },
  { key: '4', label: 'Pacheco' },
  { key: '2', label: 'Araújo' },
];

it('shows every pharmacy series when the Todas filter is active', () => {
  expect(getVisibleHistorySeries(series, null)).toEqual(series);
});

it('selects only the tapped pharmacy when narrowing from Todas', () => {
  const selected = toggleHistoryPharmacy(null, 4, series);

  expect(selected).toEqual([4]);
  expect(getVisibleHistorySeries(series, selected)).toEqual([series[1]]);
});

it('allows multiple pharmacy series and returns to Todas when all are selected', () => {
  const selectedIndiana = toggleHistoryPharmacy(null, 1, series);
  const selectedTwo = toggleHistoryPharmacy(selectedIndiana, 4, series);
  const selectedAll = toggleHistoryPharmacy(selectedTwo, 2, series);

  expect(selectedTwo).toEqual([1, 4]);
  expect(selectedAll).toBeNull();
  expect(getVisibleHistorySeries(series, selectedAll)).toEqual(series);
});

it('returns to Todas when the only selected pharmacy is tapped again', () => {
  expect(toggleHistoryPharmacy([4], 4, series)).toBeNull();
});

it('keeps current pharmacies selectable without inventing history points', () => {
  const offers = [
    { farmacia_id: 1, farmacia_nome: 'Indiana', disponivel: true, preco: 11.06 },
    { farmacia_id: 4, farmacia_nome: 'Pacheco', disponivel: true, preco: 7.99 },
    { farmacia_id: 2, farmacia_nome: 'Araújo', disponivel: false, preco: 9 },
  ];
  const historical = [{ key: '4', label: 'Pacheco', color: '#00f' }];
  const options = getHistoryPharmacyOptions(offers, historical, (offer) => offer.farmacia_nome, ['#0f0']);

  expect(options).toEqual([
    { key: '1', label: 'Indiana', color: '#0f0', hasHistory: false },
    { key: '4', label: 'Pacheco', color: '#00f', hasHistory: true },
  ]);
  expect(toggleHistoryPharmacy(null, 1, options)).toEqual([1]);
  expect(getVisibleHistorySeries(historical, [1])).toEqual([]);
});
