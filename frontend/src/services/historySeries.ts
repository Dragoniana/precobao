export type PharmacySeriesKey = { key: string };

export type HistoryPharmacyOption = { key: string; label: string; color: string; hasHistory: boolean };

export function getHistoryPharmacyOptions<
  T extends PharmacySeriesKey & { label: string; color: string },
  O extends { farmacia_id: number; disponivel: boolean; preco: number | string },
>(
  offers: O[],
  series: T[],
  getOfferName: (offer: O) => string,
  colors: string[],
): HistoryPharmacyOption[] {
  const options = new Map<number, HistoryPharmacyOption>();
  for (const offer of offers) {
    if (!offer.disponivel || Number(offer.preco) <= 0) continue;
    const id = offer.farmacia_id;
    if (!options.has(id)) {
      options.set(id, {
        key: String(id),
        label: getOfferName(offer),
        color: colors[(id - 1) % colors.length],
        hasHistory: false,
      });
    }
  }
  for (const item of series) {
    const id = Number(item.key);
    options.set(id, { key: item.key, label: item.label, color: item.color, hasHistory: true });
  }
  return [...options.values()];
}

export function getVisibleHistorySeries<T extends PharmacySeriesKey>(
  series: T[],
  selectedPharmacies: number[] | null,
): T[] {
  if (selectedPharmacies === null) return series;

  const selected = new Set(selectedPharmacies);
  return series.filter((item) => selected.has(Number(item.key)));
}

export function toggleHistoryPharmacy<T extends PharmacySeriesKey>(
  selectedPharmacies: number[] | null,
  pharmacyId: number,
  series: T[],
): number[] | null {
  if (selectedPharmacies === null) return [pharmacyId];

  const selected = new Set(selectedPharmacies);
  if (selected.has(pharmacyId)) {
    selected.delete(pharmacyId);
  } else {
    selected.add(pharmacyId);
  }

  if (selected.size === 0 || series.every((item) => selected.has(Number(item.key)))) {
    return null;
  }

  return [...selected];
}
