import { useCallback, useEffect, useRef, useState } from 'react';

import { Medicamento } from '../types/api';
import {
  CatalogQuery,
  fetchCatalogPage,
  fetchCheapestMedicines,
  fetchFilterOptions,
  fetchMedicine,
  FilterOption,
} from '../services/catalog';
import { markMedicineOpenTrace } from '../services/medicineOpenPerformance';

const DEFAULT_FILTERS: FilterOption[] = [];
const isSupportedFilter = (filter: FilterOption) =>
  ['receita', 'categoria', 'controle_especial', 'laboratorio'].includes(String(filter.type));

const CACHE_LIMIT = 180;

export function useCatalogState() {
  const cacheRef = useRef(new Map<number, Medicamento>());
  const requestsRef = useRef(new Map<number, Promise<Medicamento>>());
  const catalogRequestSerial = useRef(0);
  const [filterOptions, setFilterOptions] = useState(DEFAULT_FILTERS);
  const [selectedFilters, setSelectedFilters] = useState<FilterOption[]>([]);
  const [selectedPharmacyId, setSelectedPharmacyId] = useState<number | null>(null);
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
  }, []);

  const loadCatalogPage = useCallback(async (query: CatalogQuery) => {
    const requestSerial = ++catalogRequestSerial.current;
    const page = await fetchCatalogPage({ ...query, sortBy: query.sortBy ?? 'relevancia' });
    // Ignore stale pages so a slower response from a previous search/filter
    // cannot overwrite the shared medicine cache with older offer data.
    if (requestSerial === catalogRequestSerial.current) rememberMedicines(page.items);
    return page;
  }, [rememberMedicines]);

  const loadMedicineById = useCallback(async (id: number, force = false) => {
    // A forced refresh bypasses a successful cache entry, but still joins an
    // in-flight lookup for this ID instead of issuing a duplicate request.
    const pending = requestsRef.current.get(id);
    if (pending) return pending;
    const existing = !force ? cacheRef.current.get(id) : undefined;
    if (existing) return existing;
    markMedicineOpenTrace(id, 'api-request-start');
    const request = fetchMedicine(id).then((medicine) => {
      markMedicineOpenTrace(id, 'api-response');
      rememberMedicines([medicine]);
      return medicine;
    }).catch((error: unknown) => {
      markMedicineOpenTrace(id, 'api-error');
      throw error;
    }).finally(() => {
      if (requestsRef.current.get(id) === request) requestsRef.current.delete(id);
    });
    requestsRef.current.set(id, request);
    return request;
  }, [rememberMedicines]);

  const getMedicineById = useCallback((id: number) => {
    const medicine = cacheRef.current.get(id);
    if (medicine) {
      cacheRef.current.delete(id);
      cacheRef.current.set(id, medicine);
    }
    return medicine;
  }, []);

  const markAsViewed = useCallback((id: number) => {
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

  return {
    filterOptions,
    selectedFilters,
    selectedPharmacyId,
    setSelectedPharmacyId,
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
