import { act, renderHook, waitFor } from '@testing-library/react-native';

import { api } from '../services/api';
import { fetchCatalogPage, fetchFilterOptions, FilterOption, resolveMedicineImage, resolveMedicineImages } from '../services/catalog';
import { useCatalogState } from '../hooks/useCatalogState';
import { Medicamento } from '../types/api';

const item = {
  id: 18, name_search: 'Paracetamol', codigo_barras: '', laboratorio: 'Lab A',
  principio_ativo: 'Paracetamol', exige_receita: false, categorias: [], ofertas: [],
};

beforeEach(() => jest.clearAllMocks());

it('renders only API-resolved registry URLs without pharmacy-specific rules', () => {
  const medicine = {
    id: 1,
    codigo_barras: '7891035010866',
    nome: 'Strepsils Mel e Lim??o',
    principio_ativo: '',
    laboratorio: '',
    exige_receita: false,
    categorias: [],
    imagem_urls: [
      'https://new-pharmacy.example.test/generic-pack.jpg',
      'https://cdn.example.test/second-image.webp',
    ],
    ofertas: [{
      id: 1, farmacia_id: 1, catalogo_id: 1, preco: 10, quantidade_estoque: 1,
      disponivel: true,
      url_origem: 'https://shop.example.test/product/p',
      imagem_url: 'https://shop.example.test/legacy-database-image.jpg',
    }],
  } as Medicamento;

  expect(resolveMedicineImages(medicine)).toEqual(medicine.imagem_urls ?? []);
  expect(resolveMedicineImage(medicine)).toBe(medicine.imagem_urls?.[0]);
  expect(resolveMedicineImages({ ...medicine, imagem_urls: [] })).toEqual([]);
});

it('uses API-resolved primary image and ordered fallbacks for every screen', () => {
  const medicine = {
    id: 9,
    codigo_barras: '7896523212685',
    nome: 'Nimesulida 100mg Cimed',
    principio_ativo: '',
    laboratorio: '',
    exige_receita: false,
    categorias: [],
    imagem_url: 'https://cdn.example.test/primary.jpg',
    imagem_urls: [
      'https://cdn.example.test/primary.jpg',
      'https://another-pharmacy.example.test/fallback.webp',
    ],
    ofertas: [],
  } as Medicamento;
  expect(resolveMedicineImages(medicine)).toEqual(medicine.imagem_urls ?? []);
});

it('requests only the selected server page and serializes combined filters and search fields', async () => {
  (api.get as jest.Mock).mockResolvedValue({
    data: { total: 37, limit: 12, offset: 12, items: [item] },
  });
  const staleReference = { id: 'referencia:sim', label: 'Medicamento de referência (ANVISA)', value: 'sim', type: 'referencia' } as unknown as FilterOption;
  const staleClassification = { id: 'classificacao:desconhecida', label: 'Categoria não confirmada', value: 'desconhecida', type: 'classificacao' } as unknown as FilterOption;
  const page = await fetchCatalogPage({
    query: 'paracetamol', pharmacyId: 3, offset: 12, limit: 12,
    sortBy: 'menor_preco',
    filters: [
      { id: 'categoria:similar', label: 'Similar', value: 'similar', type: 'categoria' },
      { id: 'laboratorio:lab-a', label: 'Lab A', value: 'Lab A', type: 'laboratorio' },
      { id: 'receita:com_receita', label: 'Com receita', value: 'com_receita', type: 'receita' },
      { id: 'controle_especial:sim', label: 'Controle especial', value: 'sim', type: 'controle_especial' },
      staleReference,
      staleClassification,
    ],
  });
  const requestedUrl = (api.get as jest.Mock).mock.calls[0][0] as string;
  expect(requestedUrl).toContain('limit=12');
  expect(requestedUrl).toContain('offset=12');
  expect(requestedUrl).toContain('termo_busca=paracetamol');
  expect(requestedUrl).toContain('farmacia_id=3');
  expect(requestedUrl).toContain('ordenar=menor_preco');
  expect(requestedUrl.match(/filtros=/g)).toHaveLength(4);
  expect(requestedUrl).toContain('filtros=receita%3Acom_receita');
  expect(requestedUrl).toContain('filtros=controle_especial%3Asim');
  expect(requestedUrl).not.toContain('referencia%3A');
  expect(requestedUrl).not.toContain('classificacao%3A');
  expect(page.total).toBe(37);
  expect(page.items[0].nome).toBe('Paracetamol');
});

it('does not expose or serialize legacy active-ingredient filters while preserving general medicine search data', async () => {
  (api.get as jest.Mock).mockImplementation((url: string) => {
    if (url.startsWith('/catalogo/filtros/opcoes')) return Promise.resolve({ data: {
      categorias: ['Similar'], laboratorios: ['Cimed'], principios_ativos: ['Paracetamol'],
    } });
    return Promise.resolve({ data: { total: 1, limit: 20, offset: 0, items: [{ ...item, principio_ativo: 'Paracetamol' }] } });
  });

  const options = await fetchFilterOptions();
  expect(options.map((option) => option.type)).not.toContain('principio_ativo');
  expect(options.map((option) => option.label)).not.toContain('Paracetamol');

  const legacy = { id: 'principio_ativo:paracetamol', label: 'Paracetamol', value: 'Paracetamol', type: 'principio_ativo' } as unknown as FilterOption;
  const page = await fetchCatalogPage({ query: 'Paracetamol', filters: [legacy] });
  const url = (api.get as jest.Mock).mock.calls[1][0] as string;
  expect(url).toContain('termo_busca=Paracetamol');
  expect(url).not.toContain('filtros=principio_ativo');
  expect(page.items[0].principio_ativo).toBe('Paracetamol');
});

it('exposes only confirmed sale filters and omits unavailable or retired classifications', async () => {
  (api.get as jest.Mock).mockResolvedValue({ data: {
    categorias: ['Similar', 'Genérico', 'Biológico'],
    laboratorios: [],
    contagens: { referencia: 12, desconhecida: 9, com_receita: 8, venda_livre: 0, controle_especial: 3 },
  } });

  const options = await fetchFilterOptions();

  expect(options.map(({ label }) => label)).toEqual(expect.arrayContaining(['Com receita', 'Controle especial']));
  expect(options.map(({ label }) => label)).not.toEqual(expect.arrayContaining([
    'Venda livre', 'Medicamento de referência (ANVISA)', 'Categoria não confirmada', 'Biológico',
  ]));
  expect(options.filter(({ type }) => type === 'receita' || type === 'controle_especial')).toHaveLength(2);
});

it('keeps a bounded item cache and requests detail by identifier on demand', async () => {
  (api.get as jest.Mock).mockImplementation((url: string) => {
    if (url.startsWith('/catalogo/filtros')) return Promise.resolve({ data: {} });
    if (url.startsWith('/catalogo/mais-baratos')) return Promise.resolve({ data: [] });
    if (url.startsWith('/catalogo/medicamentos/18')) return Promise.resolve({ data: item });
    return Promise.resolve({ data: { total: 1, limit: 20, offset: 0, items: [item] } });
  });
  const { result } = await renderHook(() => useCatalogState());
  let medicine!: Medicamento;
  await act(async () => { medicine = await result.current.loadMedicineById(18); });
  expect(medicine.nome).toBe('Paracetamol');
  expect(api.get).toHaveBeenCalledWith('/catalogo/medicamentos/18');
  await waitFor(() => expect(result.current.getMedicineById(18)?.id).toBe(18));
});

it('refreshes an existing detail so changed pharmacy prices are not held by the cache', async () => {
  let currentPrice = 11.06;
  (api.get as jest.Mock).mockImplementation((url: string) => {
    if (url.startsWith('/catalogo/filtros')) return Promise.resolve({ data: {} });
    if (url.startsWith('/catalogo/mais-baratos')) return Promise.resolve({ data: [] });
    if (url.startsWith('/catalogo/medicamentos/18')) {
      return Promise.resolve({ data: {
        ...item,
        ofertas: [{ id: 7, farmacia_id: 1, catalogo_id: 18, preco: currentPrice, disponivel: true }],
      } });
    }
    return Promise.resolve({ data: {} });
  });
  const { result } = await renderHook(() => useCatalogState());
  await act(async () => { await result.current.loadMedicineById(18); });
  expect(result.current.getMedicineById(18)?.ofertas[0].preco).toBe(11.06);

  currentPrice = 8.63;
  await act(async () => { await result.current.loadMedicineById(18, true); });
  expect(result.current.getMedicineById(18)?.ofertas[0].preco).toBe(8.63);
});

it('joins concurrent forced detail refreshes for the same medicine ID', async () => {
  let resolveDetail!: (value: { data: typeof item }) => void;
  (api.get as jest.Mock).mockImplementation((url: string) => {
    if (url.startsWith('/catalogo/filtros')) return Promise.resolve({ data: {} });
    if (url.startsWith('/catalogo/mais-baratos')) return Promise.resolve({ data: [] });
    if (url === '/catalogo/medicamentos/18') return new Promise((resolve) => { resolveDetail = resolve; });
    return Promise.resolve({ data: {} });
  });

  const { result } = await renderHook(() => useCatalogState());
  let first!: Promise<Medicamento>;
  let second!: Promise<Medicamento>;
  await act(async () => {
    first = result.current.loadMedicineById(18, true);
    second = result.current.loadMedicineById(18, true);
  });

  expect((api.get as jest.Mock).mock.calls.filter(([url]) => url === '/catalogo/medicamentos/18')).toHaveLength(1);

  await act(async () => {
    resolveDetail({ data: item });
    await Promise.all([first, second]);
  });
  expect(result.current.getMedicineById(18)?.nome).toBe('Paracetamol');
});

it('does not let a slower previous catalog request overwrite the medicine cache', async () => {
  let resolveOlder!: (value: { data: { total: number; limit: number; offset: number; items: typeof item[] } }) => void;
  let resolveNewer!: (value: { data: { total: number; limit: number; offset: number; items: typeof item[] } }) => void;
  const older = new Promise<{ data: { total: number; limit: number; offset: number; items: typeof item[] } }>((resolve) => { resolveOlder = resolve; });
  const newer = new Promise<{ data: { total: number; limit: number; offset: number; items: typeof item[] } }>((resolve) => { resolveNewer = resolve; });
  (api.get as jest.Mock).mockImplementation((url: string) => {
    if (url.startsWith('/catalogo?')) return url.includes('termo_busca=older') ? older : newer;
    if (url.startsWith('/catalogo/filtros')) return Promise.resolve({ data: {} });
    if (url.startsWith('/catalogo/mais-baratos')) return Promise.resolve({ data: [] });
    return Promise.resolve({ data: {} });
  });

  const { result } = await renderHook(() => useCatalogState());
  let olderRequest!: Promise<unknown>;
  let newerRequest!: Promise<unknown>;
  await act(async () => {
    olderRequest = result.current.loadCatalogPage({ query: 'older' });
    newerRequest = result.current.loadCatalogPage({ query: 'newer' });
  });

  await act(async () => {
    resolveNewer({ data: { total: 1, limit: 20, offset: 0, items: [{ ...item, name_search: 'Newest result' }] } });
    await newerRequest;
  });
  await act(async () => {
    resolveOlder({ data: { total: 1, limit: 20, offset: 0, items: [{ ...item, name_search: 'Older result' }] } });
    await olderRequest;
  });

  await waitFor(() => expect(result.current.getMedicineById(18)?.nome).toBe('Newest result'));
});
