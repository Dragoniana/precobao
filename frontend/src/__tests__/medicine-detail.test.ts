import { formatPrice, getComparableOffers, getMedicineDetails, normalizeOfferUrl } from '../app/(app)/medicine/[id]';
import { Medicamento, Oferta } from '../types/api';

const medicine: Medicamento = {
  id: 12,
  codigo_barras: '7891234567890',
  nome: 'Medicamento de exemplo',
  principio_ativo: 'Subst\u00e2ncia ativa',
  laboratorio: 'Laborat\u00f3rio exemplo',
  exige_receita: false,
  categorias: [],
  categoria_regulatoria: 'Similar',
  referencia_anvisa: false,
  exige_receita_confirmada: null,
  ofertas: [],
};

it('keeps the detail description focused on useful product information', () => {
  expect(getMedicineDetails(medicine, 'Laborat\u00f3rio amig\u00e1vel')).toEqual([
    { label: 'Princ\u00edpio ativo', value: 'Subst\u00e2ncia ativa' },
    { label: 'Laborat\u00f3rio', value: 'Laborat\u00f3rio amig\u00e1vel' },
    { label: 'C\u00f3digo de barras', value: '7891234567890' },
  ]);
});

it('omits missing details instead of showing technical placeholders', () => {
  expect(getMedicineDetails({ ...medicine, codigo_barras: '', principio_ativo: '' })).toEqual([
    { label: 'Laborat\u00f3rio', value: 'Laborat\u00f3rio exemplo' },
  ]);
  expect(getMedicineDetails(undefined)).toEqual([]);
});

const offer = (overrides: Partial<Oferta> = {}): Oferta => ({
  id: 1,
  farmacia_id: 1,
  catalogo_id: 12,
  preco: '10.00',
  quantidade_estoque: 1,
  disponivel: true,
  url_origem: 'https://farmacia.example/produto-12',
  farmacia_nome: 'Farmácia A',
  ...overrides,
});

it('keeps the single valid current offer for the medicine', () => {
  expect(getComparableOffers([offer()])).toEqual([offer()]);
});

it('formats current comparison prices as Brazilian currency', () => {
  expect(formatPrice(2411.28)).toBe('R$\u00a02.411,28');
});

it('sorts by current price, identifies the same lowest offer, and keeps distinct pharmacies', () => {
  const third = offer({ id: 3, farmacia_id: 3, farmacia_nome: 'Farmácia C', preco: 16 });
  const first = offer({ id: 1, farmacia_id: 1, farmacia_nome: 'Farmácia A', preco: 8.5 });
  const second = offer({ id: 2, farmacia_id: 2, farmacia_nome: 'Farmácia B', preco: 12 });

  expect(getComparableOffers([third, second, first])).toEqual([first, second, third]);
});

it('shows the cheapest valid offer when a pharmacy has duplicate offers', () => {
  const cheapest = offer({ id: 2, preco: 8, url_origem: 'https://farmacia.example/mais-barato' });
  const duplicate = offer({ id: 1, preco: 11 });
  expect(getComparableOffers([duplicate, cheapest])).toEqual([cheapest]);
});

it('ignores unavailable or invalid prices without discarding offers that lack a URL', () => {
  const withoutUrl = offer({ id: 4, farmacia_id: 4, preco: 4.25, url_origem: '' });
  expect(getComparableOffers([
    offer({ id: 1, preco: 0 }),
    offer({ id: 2, preco: 'NaN' }),
    offer({ id: 3, disponivel: false, preco: 1 }),
    withoutUrl,
  ])).toEqual([withoutUrl]);
});

it('keeps each pharmacy offer URL separate and leaves a missing URL unavailable', () => {
  const indiana = offer({ farmacia_id: 1, farmacia_nome: 'Indiana', url_origem: 'https://indiana.example/item' });
  const araujo = offer({ id: 2, farmacia_id: 2, farmacia_nome: 'Araujo', url_origem: 'https://araujo.example/item' });
  const noUrl = offer({ id: 3, farmacia_id: 3, url_origem: '' });

  expect(getComparableOffers([indiana, araujo, noUrl]).map((item) => normalizeOfferUrl(item.url_origem))).toEqual([
    'https://indiana.example/item',
    'https://araujo.example/item',
    '',
  ]);
});
