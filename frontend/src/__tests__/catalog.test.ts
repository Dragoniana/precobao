import { act, renderHook, waitFor } from '@testing-library/react-native';

import { API_URL, api } from '../services/api';
import { fetchCatalogPage, fetchFilterOptions, FilterOption, resolveMedicineImage, resolveMedicineImages } from '../services/catalog';
import { useCatalogState } from '../hooks/useCatalogState';
import { Medicamento } from '../types/api';

const item = {
  id: 18, name_search: 'Paracetamol', codigo_barras: '', laboratorio: 'Lab A',
  principio_ativo: 'Paracetamol', exige_receita: false, categorias: [], ofertas: [],
};

beforeEach(() => jest.clearAllMocks());

it('accepts offer supplied generic images and rejects technically unsafe or mismatched assets', () => {
  const medicine = {
    id: 1, codigo_barras: '7891035010866', nome: 'Strepsils Mel e Limão 16 Pastilhas',
    principio_ativo: '', laboratorio: '', exige_receita: false, categorias: [],
    ofertas: [{
      id: 1, farmacia_id: 1, catalogo_id: 1, preco: 10, quantidade_estoque: 1,
      disponivel: true, url_origem: 'https://www.farmaciaindiana.com.br/strepsils-8-75mg-sabor-mel-e-limao-16-pastilhas/p',
      imagem_url: 'https://farmaciaindiana.vteximg.com.br/arquivos/strepsils-8-75mg-sabor-mel-e-limao-16-pastilhas-1.jpg',
    }],
  };
  expect(resolveMedicineImage(medicine)).toBe(medicine.ofertas[0].imagem_url);
  expect(resolveMedicineImage({ ...medicine, ofertas: [{ ...medicine.ofertas[0], imagem_rejeitada: true }] })).toBeUndefined();
  expect(resolveMedicineImage({ ...medicine, ofertas: [{ ...medicine.ofertas[0], imagem_url: 'https://farmaciaindiana.vteximg.com.br/arquivos/imagem-strepsils-mel-limao.jpg' }] })).toContain('imagem-strepsils-mel-limao.jpg');
  expect(resolveMedicineImage({ ...medicine, ofertas: [{ ...medicine.ofertas[0], imagem_url: 'https://farmaciaindiana.vteximg.com.br/arquivos/7891035010866.jpg' }] })).toContain('7891035010866.jpg');
  expect(resolveMedicineImage({ ...medicine, ofertas: [{ ...medicine.ofertas[0], imagem_url: 'https://farmaciaindiana.vteximg.com.br/arquivos/7891035010867.jpg' }] })).toBeUndefined();
  expect(resolveMedicineImage({ ...medicine, ofertas: [{ ...medicine.ofertas[0], imagem_url: 'https://farmaciaindiana.vteximg.com.br/arquivos/MEDICAMENTOS-CAIXA-AZUL-OTC.jpg' }] })).toContain('MEDICAMENTOS-CAIXA-AZUL-OTC.jpg');
  const encodedGenericImage = 'https://farmaciaindiana.vteximg.com.br/arquivos/CAIXA%20MEDICAMENTO%20v.2.0%20-%20CIMED.jpg';
  expect(resolveMedicineImage({ ...medicine, ofertas: [{ ...medicine.ofertas[0], imagem_url: encodedGenericImage }] })).toBe(encodedGenericImage);
  expect(resolveMedicineImage({ ...medicine, imagem_status: 'pendente', imagem_ref: '/catalogo/medicamentos/1/imagem?v=hash', imagem_url: encodedGenericImage, ofertas: [] })).toBe(`${API_URL.replace(/\/+$/, '')}/catalogo/medicamentos/1/imagem?v=hash`);
  expect(resolveMedicineImage({ ...medicine, ofertas: [{ ...medicine.ofertas[0], imagem_url: 'https://cdn.example.com/strepsils.jpg' }] })).toBeUndefined();
  expect(resolveMedicineImage({
    ...medicine,
    ofertas: [{ ...medicine.ofertas[0], url_origem: 'https://www.farmaciaindiana.com.br/citrato-sildenafila-50mg-neo-quimica-generico-4-comprimidos/p', imagem_url: 'https://farmaciaindiana.vteximg.com.br/arquivos/Caixa-Neo-Quimica-Generico.jpg' }],
  })).toContain('Caixa-Neo-Quimica-Generico.jpg');
  const araujoOffer = {
    ...medicine.ofertas[0],
    farmacia_id: 2,
    url_origem: 'https://www.araujo.com.br/simeticona-75mgml-cimed-generico-gotas-com-15ml/17332.html',
    imagem_url: 'https://i3-imagens-prd.araujo.com.br/redimensionada/380x380/17332/07896523212319_1.webp',
  };
  expect(resolveMedicineImage({ ...medicine, codigo_barras: '', ofertas: [araujoOffer] })).toBe(araujoOffer.imagem_url);
  expect(resolveMedicineImage({
    ...medicine, codigo_barras: '', imagem_status: 'pendente',
    imagem_url: araujoOffer.imagem_url,
    imagem_motivo_rejeicao: 'O conteúdo é uma ilustração genérica da farmácia.',
    ofertas: [araujoOffer],
  })).toBe(araujoOffer.imagem_url);
  const newlyCollectedAraujoOffer = {
    ...araujoOffer,
    url_origem: 'https://www.araujo.com.br/produto-novo/987654.html',
    imagem_url: 'https://i3-imagens-prd.araujo.com.br/redimensionada/380x380/987654/78912345678905_1.webp',
  };
  expect(
    resolveMedicineImage({ ...medicine, codigo_barras: '', ofertas: [newlyCollectedAraujoOffer] }),
  ).toBe(newlyCollectedAraujoOffer.imagem_url);
  expect(resolveMedicineImage({
    ...medicine,
    ofertas: [{ ...araujoOffer, url_origem: 'https://www.araujo.com.br/wegovy-1mg-semaglutida-com-4-doses-injetaveis/101392.html', imagem_url: 'https://i3-imagens-prd.araujo.com.br/redimensionada/380x380/101392/231489_7897705203095_2.webp' }],
  })).toBeUndefined();
  expect(resolveMedicineImage({
    ...medicine,
    ofertas: [{
      ...araujoOffer,
      url_origem: 'https://www.araujo.com.br/glyxambi-25mg--5mg-com-30-comprimidos/7290.html',
      imagem_url: 'https://i3-imagens-prd.araujo.com.br/webp/7290/128666_7896026306751_1.webp?sw=200&sh=200&sm=fit',
      imagem_generica: true,
    }],
  })).toContain('128666_7896026306751_1.webp');
  expect(resolveMedicineImage({
    ...medicine,
    codigo_barras: '7896026307925',
    ofertas: [{
      ...medicine.ofertas[0],
      url_origem: 'https://www.farmaciaindiana.com.br/glyxambi-25mg-5mg-30-comprimidos/p',
      imagem_url: 'https://farmaciaindiana.vteximg.com.br/arquivos/ids/306093/7896026306751.jpg',
    }],
  })).toBeUndefined();
  expect(resolveMedicineImage({
    ...medicine,
    codigo_barras: '',
    ofertas: [{
      ...medicine.ofertas[0],
      farmacia_id: 2,
      url_origem: 'https://www.araujo.com.br/aciclovir-200mg-pharlab-generico-com-25-comprimidos/84977.html',
      imagem_url: 'https://i3-imagens-prd.araujo.com.br/webp/84977/120803_7898216367429_1.webp?sw=200&sh=200&sm=fit',
    }],
  })).toContain('120803_7898216367429_1.webp');
  expect(resolveMedicineImage({
    ...medicine,
    codigo_barras: '',
    ofertas: [{
      ...araujoOffer,
      url_origem: 'https://www.araujo.com.br/aciclovir/84977.html',
      imagem_url: 'https://i3-imagens-prd.araujo.com.br/webp/12345/specific-image.webp',
    }],
  })).toBeUndefined();
  expect(resolveMedicineImage({
    ...medicine,
    codigo_barras: '7898581711254',
    ofertas: [{
      ...medicine.ofertas[0],
      farmacia_nome: 'Nissei',
      url_origem: 'https://www.farmaciasnissei.com.br/neuleptil-10mg-20ml/p',
      imagem_url: 'https://www.farmaciasnissei.com.br/media/medicamentos/caixa-medicamento-geral-tarja-vermelha.webp',
      imagem_generica: true,
    }],
  })).toContain('caixa-medicamento-geral-tarja-vermelha.webp');
});

it('keeps a safe offer image after a catalog redirect fails', () => {
  const medicine = {
    id: 9, nome: 'Nimesulida 100mg Cimed', codigo_barras: '7896523212685',
    principio_ativo: '', laboratorio: '', exige_receita: false, categorias: [],
    imagem_status: 'verificada', imagem_ref: '/catalogo/medicamentos/9/imagem',
    imagem_url: 'https://i3-imagens-prd.araujo.com.br/webp/3848/7896523212685_1.webp',
    ofertas: [{
      id: 1, farmacia_id: 1, catalogo_id: 9, preco: 5, quantidade_estoque: 1,
      disponivel: true,
      url_origem: 'https://www.farmaciaindiana.com.br/nimesulida-100mg-cimed-generico-12-comprimidos/p',
      imagem_url: 'https://farmaciaindiana.vteximg.com.br/arquivos/nimesulida-100mg-cimed-generico-12-comprimidos.jpg',
    }],
  };
  expect(resolveMedicineImages(medicine)).toEqual([
    `${API_URL.replace(/\/+$/, '')}${medicine.imagem_ref}`,
    medicine.ofertas[0].imagem_url,
  ]);
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
