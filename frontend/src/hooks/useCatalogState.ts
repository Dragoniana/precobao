import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { Medicamento } from '../types/api';
import {
  CatalogQuery,
  fetchCatalogPage,
  fetchCheapestMedicines,
  fetchFilterOptions,
  fetchMedicine,
  FilterOption,
} from '../services/catalog';

const DEFAULT_FILTERS: FilterOption[] = [];
const isSupportedFilter = (filter: FilterOption) =>
  ['receita', 'categoria', 'controle_especial', 'laboratorio'].includes(String(filter.type));

const CACHE_LIMIT = 180;

export function useCatalogState() {
  const cacheRef = useRef(new Map<number, Medicamento>());
  const requestsRef = useRef(new Map<number, Promise<Medicamento>>());
  const [cache, setCache] = useState<Medicamento[]>([]);
  const [filterOptions, setFilterOptions] = useState(DEFAULT_FILTERS);
  const [selectedFilters, setSelectedFilters] = useState<FilterOption[]>([]);
  const [selectedPharmacyId, setSelectedPharmacyId] = useState<number | null>(null);
  const [recentIds, setRecentIds] = useState<number[]>([]);
  const [cheapestMedicines, setCheapestMedicines] = useState<Medicamento[]>([]);
  const [optionsError, setOptionsError] = useState(false);
  const [cheapestError, setCheapestError] = useState(false);

  const rememberMedicines = useCallback((items: Medicamento[]) => {
    const current = cacheRef.current;
    items.forEach((item) => {
      current.delete(item.id);
      current.set(item.id, item);
    });
    while (current.size > CACHE_LIMIT) {
      const oldestId = current.keys().next().value;
      if (oldestId === undefined) break;
      current.delete(oldestId);
    }
    setCache([...current.values()]);
  }, []);

  const loadCatalogPage = useCallback(async (query: CatalogQuery) => {
    const page = await fetchCatalogPage({ ...query, sortBy: query.sortBy ?? 'relevancia' });
    rememberMedicines(page.items);
    return page;
  }, [rememberMedicines]);

  const loadMedicineById = useCallback(async (id: number, force = false) => {
    const existing = !force ? cacheRef.current.get(id) : undefined;
    if (existing) return existing;
    const pending = requestsRef.current.get(id);
    if (pending && !force) return pending;
    const request = fetchMedicine(id).then((medicine) => {
      rememberMedicines([medicine]);
      return medicine;
    }).finally(() => requestsRef.current.delete(id));
    requestsRef.current.set(id, request);
    return request;
  }, [rememberMedicines]);

  const getMedicineById = useCallback(
    (id: number) => cache.find((medicine) => medicine.id === id),
    [cache]
  );

  const markAsViewed = useCallback((id: number) => {
    setRecentIds((current) => [id, ...current.filter((item) => item !== id)].slice(0, 6));
    if (!cacheRef.current.has(id)) void loadMedicineById(id).catch(() => undefined);
  }, [loadMedicineById]);

  const toggleFilter = useCallback((filter: FilterOption) => {
    if (!isSupportedFilter(filter)) return;
    setSelectedFilters((current) => current.some((item) => item.id === filter.id)
      ? current.filter((item) => item.id !== filter.id)
      : [...current, filter]);
  }, []);

  const applyFilters = useCallback((filters: FilterOption[]) => setSelectedFilters(filters.filter(isSupportedFilter)), []);
  const clearFilters = useCallback(() => setSelectedFilters([]), []);

  useEffect(() => {
    setSelectedFilters((current) => current.some((filter) => !isSupportedFilter(filter))
      ? current.filter(isSupportedFilter)
      : current);
  }, []);

  useEffect(() => {
    let active = true;
    fetchFilterOptions().then((options) => {
      if (active) { setFilterOptions(options); setOptionsError(false); }
    }).catch(() => { if (active) setOptionsError(true); });
    fetchCheapestMedicines().then((items) => {
      if (active) { setCheapestMedicines(items); rememberMedicines(items); setCheapestError(false); }
    }).catch(() => { if (active) setCheapestError(true); });
    return () => { active = false; };
  }, [rememberMedicines]);

  const recentMedicines = useMemo(
    () => recentIds.map((id) => cache.find((medicine) => medicine.id === id)).filter((item): item is Medicamento => Boolean(item)),
    [cache, recentIds]
  );

  return {
    filterOptions,
    selectedFilters,
    selectedPharmacyId,
    setSelectedPharmacyId,
    recentIds,
    recentMedicines,
    cheapestMedicines,
    optionsError,
    cheapestError,
    loadCatalogPage,
    loadMedicineById,
    getMedicineById,
    rememberMedicines,
    markAsViewed,
    toggleFilter,
    applyFilters,
    clearFilters,
  };
}
