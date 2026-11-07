import { API_URL, api } from './api';
import { Medicamento } from '../types/api';

export type FilterKind = 'receita' | 'categoria' | 'controle_especial' | 'laboratorio';
export type FilterOption = { id: string; label: string; value: string; type: FilterKind };
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

export function normalizeText(value: unknown) {
  return String(value ?? '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().trim();
}

const IMAGE_PHARMACY_DOMAINS: Record<string, string> = {
  'farmaciaindiana.vteximg.com.br': 'farmaciaindiana.com.br',
  'i3-imagens-prd.araujo.com.br': 'araujo.com.br',
  'www.farmaciasnissei.com.br': 'farmaciasnissei.com.br',
  'farmaciamodelo.com.br': 'farmaciamodelo.com.br',
  'www.farmaciamodelo.com.br': 'farmaciamodelo.com.br',
  'drogariaspacheco.vteximg.com.br': 'drogariaspacheco.com.br',
};
function imageBelongsToOffer(
  imageUrl: string,
  productUrl: string,
  medicine: Medicamento,
  allowGenericEanMismatch: boolean,
) {
  try {
    const image = new URL(imageUrl);
    const product = new URL(productUrl);
    if (image.protocol !== 'https:' || product.protocol !== 'https:') return false;
    if (image.username || image.password || product.username || product.password) return false;
    if (image.port && image.port !== '443') return false;
    const pharmacyDomain = IMAGE_PHARMACY_DOMAINS[image.hostname.toLowerCase()];
    if (!pharmacyDomain) return false;

    const imageHost = image.hostname.toLowerCase();
    const productHost = product.hostname.toLowerCase();
    if (productHost !== pharmacyDomain && !productHost.endsWith(`.${pharmacyDomain}`)) return false;

    const filename = decodeURIComponent(image.pathname.split('/').pop() || '');
    if (!/\.(jpe?g|png|webp)$/i.test(filename)) return false;
    if (imageHost.includes('araujo')) {
      const productId = product.pathname.match(/\/(\d+)\.html?$/i)?.[1];
      const imageParts = image.pathname.split('/').filter(Boolean);
      const imageProductId = imageParts.length > 1 ? imageParts[imageParts.length - 2] : undefined;
      if (productId && /^\d+$/.test(imageProductId || '') && imageProductId !== productId) return false;
    }
    const imageEans: string[] = filename.match(/(?<!\d)\d{13,14}(?!\d)/g) || [];
    if (
      medicine.codigo_barras
      && imageEans.length
      && !imageEans.includes(medicine.codigo_barras)
      && !allowGenericEanMismatch
    ) return false;

    // A same-pharmacy image attached to this exact catalog offer is useful even
    // when its name has no SKU/EAN or the pharmacy uses generic artwork.
    return true;
  } catch {
    return false;
  }
}
export function resolveMedicineImages(medicine: Medicamento): string[] {
  const verifiedCatalogImage: string[] = [];
  const unclassifiedCatalogImage: string[] = [];
  const deferredCatalogImage: string[] = [];
  const specificOfferImages: string[] = [];
  const genericOfferImages: string[] = [];
  if (medicine.imagem_ref?.startsWith('/catalogo/medicamentos/')) {
    const catalogImage = `${API_URL.replace(/\/+$/, '')}${medicine.imagem_ref}`;
    if (normalizeText(medicine.imagem_motivo_rejeicao).includes('generica')) {
      deferredCatalogImage.push(catalogImage);
    } else if (medicine.imagem_status === 'verificada') {
      verifiedCatalogImage.push(catalogImage);
    } else {
      unclassifiedCatalogImage.push(catalogImage);
    }
  }
  for (const offer of medicine.ofertas || []) {
    const imageUrl = offer.imagem_url?.trim();
    const productUrl = offer.url_origem?.trim();
    if (!imageUrl || !productUrl || offer.imagem_rejeitada || offer.catalogo_id !== medicine.id) continue;
    if (imageBelongsToOffer(imageUrl, productUrl, medicine, Boolean(offer.imagem_generica))) {
      (offer.imagem_generica ? genericOfferImages : specificOfferImages).push(imageUrl);
    }
  }
  return [...new Set([
    ...verifiedCatalogImage,
    ...specificOfferImages,
    ...unclassifiedCatalogImage,
    ...genericOfferImages,
    ...deferredCatalogImage,
  ])];
}
export function resolveMedicineImage(medicine: Medicamento): string | undefined {
  return resolveMedicineImages(medicine)[0];
}

export function normalizeMedicine(raw: Partial<Medicamento> & { name_search?: string }): Medicamento {
  const nome = raw.nome || raw.name_search || '';
  return {
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
    imagem_ref: raw.imagem_ref || null,
    imagem_fonte: raw.imagem_fonte || null,
    imagem_status: raw.imagem_status || null,
    imagem_motivo_rejeicao: raw.imagem_motivo_rejeicao || null,
    ofertas: Array.isArray(raw.ofertas) ? raw.ofertas : [],
  };
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
    ...(counts.com_receita ? [{ id: 'receita:com_receita', label: 'Com receita', value: 'com_receita', type: 'receita' as const }] : []),
    ...(counts.venda_livre ? [{ id: 'receita:venda_livre', label: 'Venda livre', value: 'venda_livre', type: 'receita' as const }] : []),
    ...(counts.controle_especial ? [{ id: 'controle_especial:sim', label: 'Controle especial', value: 'sim', type: 'controle_especial' as const }] : []),
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
  for (const filter of query.filters || []) {
    if (!['receita', 'categoria', 'controle_especial', 'laboratorio'].includes(String(filter.type))) continue;
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
