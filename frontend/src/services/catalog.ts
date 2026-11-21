import { API_URL, api } from './api';
import { Medicamento } from '../types/api';

export type FilterKind = 'tipo_venda' | 'receita' | 'categoria' | 'controle_especial' | 'laboratorio';
export type FilterOption = { id: string; label: string; value: string; type: FilterKind; count?: number };
export type CatalogSort = 'relevancia' | 'menor_preco' | 'maior_preco' | 'nome';
export type CatalogPage = { total: number; limit: number; offset: number; items: Medicamento[] };
export type CatalogQuery = {
  query?: string;
  pharmacyId?: number;
  offset?: number;
  limit?: number;
  filters?: FilterOption[];
  sortBy?: CatalogSort;
};

const saleTypeValues = new Set(['venda_livre', 'com_receita', 'controle_especial']);

export function saleTypeMode(filter: FilterOption): string | null {
  const value = String(filter.value).trim().toLowerCase();
  if (filter.type === 'tipo_venda' && saleTypeValues.has(value)) return value;
  if (filter.type === 'receita' && value === 'venda_livre') return 'venda_livre';
  if (filter.type === 'receita' && ['com_receita', 'exige_receita'].includes(value)) return 'com_receita';
  if (filter.type === 'controle_especial' && ['sim', 'true'].includes(value)) return 'controle_especial';
  return null;
}

export function isSaleTypeFilter(filter: FilterOption): boolean {
  return saleTypeMode(filter) !== null;
}

/** One sale type can be active at a time; category and laboratory filters remain multi-select. */
export function toggleFilterSelection(current: FilterOption[], option: FilterOption): FilterOption[] {
  if (current.some((item) => item.id === option.id)) {
    return current.filter((item) => item.id !== option.id);
  }
  if (isSaleTypeFilter(option)) {
    return [...current.filter((item) => !isSaleTypeFilter(item)), option];
  }
  return [...current, option];
}

const normalizedFields = new WeakMap<Medicamento, Set<string>>();

export function normalizeText(value: unknown) {
  return String(value ?? '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().trim();
}

export function resolveMedicineImages(medicine: Medicamento): string[] {
  const urls = Array.isArray(medicine.imagem_urls) ? medicine.imagem_urls : [];
  const legacyRef = medicine.imagem_ref?.startsWith('/catalogo/medicamentos/')
    ? `${API_URL.replace(/\/+$/, '')}${medicine.imagem_ref}` : undefined;
  const specificCandidates = medicine.imagem_armazenada && legacyRef
    ? [legacyRef, ...urls, medicine.imagem_url]
    : [...urls, medicine.imagem_url, legacyRef];
  const offerFallbacks = medicine.ofertas
    .filter((offer) => offer.imagem_generica && offer.imagem_ref)
    .map((offer) => offer.imagem_ref!.startsWith('/')
      ? `${API_URL.replace(/\/+$/, '')}${offer.imagem_ref}`
      : offer.imagem_ref!);
  const validHttps = (ordered: (string | null | undefined)[]) => [...new Set(ordered
    .filter((url): url is string => typeof url === 'string' && /^https:\/\//i.test(url.trim()))
    .map((url) => url.trim()))];

  // Bound failed network attempts while still reserving slots for offer images.
  // Catalog-specific images always get tried before pharmacy generic fallbacks.
  const specific = validHttps(specificCandidates).slice(0, 3);
  const generic = validHttps(offerFallbacks).slice(0, 2);
  return [...new Set([...specific, ...generic])];
}

export function isGenericOfferImage(medicine: Medicamento, url: string | undefined): boolean {
  if (!url) return false;
  return medicine.ofertas.some((offer) => {
    if (!offer.imagem_generica || !offer.imagem_ref) return false;
    const resolved = offer.imagem_ref.startsWith('/')
      ? `${API_URL.replace(/\/+$/, '')}${offer.imagem_ref}`
      : offer.imagem_ref;
    return resolved === url;
  });
}
export function resolveMedicineImage(medicine: Medicamento): string | undefined {
  return resolveMedicineImages(medicine)[0];
}

export function normalizeMedicine(raw: Partial<Medicamento> & { name_search?: string }): Medicamento {
  const nome = raw.nome || raw.name_search || '';
  const medicine: Medicamento = {
    id: Number(raw.id),
    codigo_barras: raw.codigo_barras || '',
    nome,
    name_search: raw.name_search || nome,
    principio_ativo: raw.principio_ativo || '',
    laboratorio: raw.laboratorio || '',
    exige_receita: Boolean(raw.exige_receita),
    categoria_regulatoria: raw.categoria_regulatoria ?? null,
    referencia_anvisa: raw.referencia_anvisa ?? null,
    exige_receita_confirmada: raw.exige_receita_confirmada ?? null,
    controle_especial: raw.controle_especial ?? null,
    classificacao_fonte: raw.classificacao_fonte ?? null,
    categorias: Array.isArray(raw.categorias) ? raw.categorias.map(String) : [],
    imagem_url: raw.imagem_url || null,
    imagem_urls: Array.isArray(raw.imagem_urls) ? raw.imagem_urls.filter((url): url is string => typeof url === 'string') : [],
    imagem_ref: raw.imagem_ref || null,
    imagem_fonte: raw.imagem_fonte || null,
    imagem_status: raw.imagem_status || null,
    imagem_motivo_rejeicao: raw.imagem_motivo_rejeicao || null,
    imagem_armazenada: Boolean(raw.imagem_armazenada),
    ofertas: Array.isArray(raw.ofertas) ? raw.ofertas : [],
  };
  normalizedFields.set(medicine, new Set(Object.keys(raw)));
  return medicine;
}

function hasUsefulCatalogValue(value: string | null | undefined) {
  const normalized = normalizeText(value);
  return Boolean(normalized && !/^(nao informado|indeterminado|pendente|n\/a)(\b|\s*\().*/.test(normalized));
}

/** Keep richer invariant fields when a response omits them, while refreshing present offers/images. */
export function mergeCatalogMedicine(existing: Medicamento | undefined, incoming: Medicamento): Medicamento {
  if (!existing) return incoming;
  const incomingFields = normalizedFields.get(incoming) ?? new Set<string>();
  const existingFields = normalizedFields.get(existing) ?? new Set<string>();
  const merged: Medicamento = { ...existing, ...incoming };

  for (const key of ['codigo_barras', 'principio_ativo', 'laboratorio'] as const) {
    if (!hasUsefulCatalogValue(incoming[key]) && hasUsefulCatalogValue(existing[key])) {
      merged[key] = existing[key] as never;
    }
  }
  if (!incoming.nome.trim() && existing.nome.trim()) merged.nome = existing.nome;
  if (!incoming.name_search?.trim() && existing.name_search?.trim()) merged.name_search = existing.name_search;

  const imageFields = ['imagem_url', 'imagem_urls', 'imagem_ref', 'imagem_fonte', 'imagem_status', 'imagem_motivo_rejeicao', 'imagem_armazenada'];
  if (!imageFields.some((field) => incomingFields.has(field))) {
    for (const field of imageFields as (keyof Medicamento)[]) {
      if (field in existing) (merged as unknown as Record<string, unknown>)[field] = existing[field];
    }
  }
  if (!incomingFields.has('ofertas')) merged.ofertas = existing.ofertas;
  if (!incomingFields.has('categorias')) merged.categorias = existing.categorias;
  for (const field of ['categoria_regulatoria', 'referencia_anvisa', 'exige_receita_confirmada', 'controle_especial', 'classificacao_fonte'] as const) {
    if (!incomingFields.has(field)) {
      (merged as unknown as Record<string, unknown>)[field] = existing[field];
    }
  }

  normalizedFields.set(merged, new Set([...existingFields, ...incomingFields]));
  return merged;
}

function normalizeUnique(values: unknown[]) {
  const unique = new Map<string, string>();
  for (const value of values) {
    const label = String(value ?? '').trim();
    const key = normalizeText(label);
    if (label && key !== 'nao informado' && key !== 'indeterminado' && !key.includes('pendente')) {
      unique.set(key, label);
    }
  }
  return [...unique.values()].sort((a, b) => a.localeCompare(b, 'pt-BR'));
}

function stripLaboratoryIdentifier(value: string) {
  const match = value.match(/^\s*([^|–—]+?)\s+[-–—|]\s+(.+?)\s*$/);
  if (!match || !/^[\d\s./()-]+$/.test(match[1])) return value.trim();

  const identifierDigits = match[1].replace(/\D/g, '');
  return identifierDigits.length >= 8 ? match[2].trim() : value.trim();
}

const LABORATORY_WORDS: Record<string, string> = {
  industria: 'Indústria',
  farmaceutica: 'Farmacêutica',
  farmaceutico: 'Farmacêutico',
  farmaceuticos: 'Farmacêuticos',
  quimica: 'Química',
  laboratorio: 'Laboratório',
  laboratorios: 'Laboratórios',
};

const LABORATORY_ACRONYMS = new Set(['EMS', 'GSK', 'MSD', 'UCB', 'FQM']);
const LOWERCASE_PARTICLES = new Set(['da', 'das', 'de', 'do', 'dos', 'e']);
const CURRENT_LABORATORY_SOURCE_LIMIT = 80;

function formatLaboratoryWord(word: string, index: number) {
  const normalized = normalizeText(word.replace(/[.,]$/, ''));
  const punctuation = word.match(/[.,]+$/)?.[0] ?? '';
  const knownWord = LABORATORY_WORDS[normalized];

  if (knownWord) return `${knownWord}${punctuation}`;
  if (/^s\.?\/?a\.?$/i.test(word)) return 'S.A.';
  if (normalized === 'ltda') return 'Ltda.';
  if (index > 0 && LOWERCASE_PARTICLES.has(normalized)) return `${normalized}${punctuation}`;
  if (LABORATORY_ACRONYMS.has(word.toUpperCase()) || /\d/.test(word)) return word;

  return `${word.charAt(0).toLocaleUpperCase('pt-BR')}${word.slice(1).toLocaleLowerCase('pt-BR').replace(/[.,]+$/, '')}${punctuation}`;
}

/** Formats only the visible laboratory name; the original string remains the API filter value. */
export function formatLaboratoryLabel(value: string) {
  const name = stripLaboratoryIdentifier(value).replace(/\s+/g, ' ').trim();
  const label = name.split(' ').map(formatLaboratoryWord).join(' ');

  // Some existing API values arrive cut at the source's 80-character limit.
  return value.length >= CURRENT_LABORATORY_SOURCE_LIMIT ? `${label}…` : label;
}

/** Returns a user-facing name only when the source contains a real laboratory value. */
export function getLaboratoryDisplayName(value?: string | null) {
  const trimmed = value?.trim();
  if (!trimmed) return undefined;
  const sourceName = stripLaboratoryIdentifier(trimmed);
  if (['nao informado', 'indeterminado', 'pendente'].includes(normalizeText(sourceName))) return undefined;
  return formatLaboratoryLabel(trimmed);
}

function createLaboratoryOptions(values: unknown[]): FilterOption[] {
  const uniqueValues = [...new Set(values.map((value) => String(value ?? '')).filter((value) => value.trim().length > 0))];
  const options = uniqueValues.map((value) => ({ value, label: formatLaboratoryLabel(value) }));
  const counts = new Map<string, number>();

  for (const option of options) {
    const key = normalizeText(option.label);
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }

  const positions = new Map<string, number>();
  return options
    .sort((a, b) => a.label.localeCompare(b.label, 'pt-BR') || a.value.localeCompare(b.value, 'pt-BR'))
    .map(({ value, label }) => {
      const key = normalizeText(label);
      const position = (positions.get(key) ?? 0) + 1;
      positions.set(key, position);
      const distinctLabel = counts.get(key)! > 1 ? `${label} · unidade ${position}` : label;

      return {
        id: `laboratorio:${encodeURIComponent(value)}`,
        label: distinctLabel,
        value,
        type: 'laboratorio' as const,
      };
    });
}

export async function fetchFilterOptions(): Promise<FilterOption[]> {
  const { data } = await api.get<{ categorias?: string[]; laboratorios?: string[]; contagens?: Record<string, number> }>('/catalogo/filtros/opcoes');
  const counts = data.contagens || {};
  const fixed: FilterOption[] = [
    { id: 'tipo_venda:venda_livre', label: 'Venda livre', value: 'venda_livre', type: 'tipo_venda', count: counts.venda_livre ?? 0 },
    { id: 'tipo_venda:com_receita', label: 'Com receita', value: 'com_receita', type: 'tipo_venda', count: counts.com_receita ?? 0 },
    { id: 'tipo_venda:controle_especial', label: 'Controle especial', value: 'controle_especial', type: 'tipo_venda', count: counts.controle_especial ?? 0 },
  ];
  const dynamic: FilterOption[] = [
    ...normalizeUnique(data.categorias || []).map((value) => ({ id: `categoria:${normalizeText(value)}`, label: value, value, type: 'categoria' as const })),
    ...createLaboratoryOptions(data.laboratorios || []),
  ];
  return [...new Map([...fixed, ...dynamic].map((item) => [item.id, item])).values()];
}

export async function fetchCatalogPage(query: CatalogQuery = {}): Promise<CatalogPage> {
  const params = new URLSearchParams();
  params.set('limit', String(query.limit ?? 20));
  params.set('offset', String(query.offset ?? 0));
  params.set('ordenar', query.sortBy ?? 'relevancia');
  if (query.query?.trim()) params.set('termo_busca', query.query.trim());
  if (query.pharmacyId !== undefined) params.set('farmacia_id', String(query.pharmacyId));
  const filters = query.filters || [];
  const activeSaleType = filters.filter(isSaleTypeFilter).at(-1);
  const normalizedFilters = [
    ...filters.filter((filter) => !isSaleTypeFilter(filter)),
    ...(activeSaleType ? [activeSaleType] : []),
  ];
  for (const filter of normalizedFilters) {
    const mode = saleTypeMode(filter);
    if (mode) {
      params.append('filtros', `tipo_venda:${mode}`);
      continue;
    }
    if (!['categoria', 'laboratorio'].includes(String(filter.type))) continue;
    params.append('filtros', `${filter.type}:${filter.value}`);
  }
  const { data } = await api.get<{ total: number; limit: number; offset: number; items: (Partial<Medicamento> & { name_search?: string })[] }>(`/catalogo?${params}`);
  return { ...data, items: data.items.map(normalizeMedicine) };
}

export async function fetchMedicine(id: number): Promise<Medicamento> {
  const { data } = await api.get<Partial<Medicamento> & { name_search?: string }>(`/catalogo/medicamentos/${id}`);
  return normalizeMedicine(data);
}

export async function fetchCheapestMedicines(limit = 6): Promise<Medicamento[]> {
  const { data } = await api.get<(Partial<Medicamento> & { name_search?: string })[]>(`/catalogo/mais-baratos?limit=${limit}`);
  return data.map(normalizeMedicine);
}
