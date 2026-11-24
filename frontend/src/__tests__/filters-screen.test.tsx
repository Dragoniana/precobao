import React from 'react';
import { fireEvent, render } from '@testing-library/react-native';

import FiltersScreen from '../app/(app)/filters';
import { api } from '../services/api';

const mockCatalog = {
  selectedFilters: [],
  filterOptions: [
    { id: 'categoria:similar', type: 'categoria', label: 'Similar', value: 'similar' },
    { id: 'categoria:biologico', type: 'categoria', label: 'Biológico', value: 'biologico' },
    { id: 'tipo_venda:venda_livre', type: 'tipo_venda', label: 'Venda livre', value: 'venda_livre', count: 0 },
    { id: 'tipo_venda:com_receita', type: 'tipo_venda', label: 'Com receita', value: 'com_receita', count: 2 },
    { id: 'tipo_venda:controle_especial', type: 'tipo_venda', label: 'Controle especial', value: 'controle_especial', count: 1 },
    { id: 'referencia:sim', type: 'referencia', label: 'Medicamento de referência (ANVISA)', value: 'sim' },
    { id: 'classificacao:desconhecida', type: 'classificacao', label: 'Categoria não confirmada', value: 'desconhecida' },
    { id: 'laboratorio:cimed', type: 'laboratorio', label: 'Cimed', value: 'CIMED' },
  ],
  applyFilters: jest.fn(),
  clearFilters: jest.fn(),
  selectedPharmacyId: null,
  setSelectedPharmacyId: jest.fn(),
};

jest.mock('../context/AppContext', () => ({ useCatalogContext: () => mockCatalog }));
jest.mock('../components/Screen', () => ({ Screen: ({ children }: { children: React.ReactNode }) => <>{children}</> }));
jest.mock('expo-router', () => ({ useRouter: () => ({ canGoBack: () => true, back: jest.fn(), replace: jest.fn() }) }));

beforeEach(() => {
  jest.clearAllMocks();
  (api.get as jest.Mock).mockResolvedValue({ data: [] });
});

it('omits active ingredient filtering and only lists supported medicine categories', async () => {
  const ui = await render(<FiltersScreen />);

  expect(ui.queryByText(/Princípios ativos/i)).toBeNull();
  expect(ui.queryByText('Paracetamol')).toBeNull();
  expect(ui.getByText('Tipo de medicamento')).toBeTruthy();
  expect(ui.queryByText('Biológico')).toBeNull();
  expect(ui.getByText('Laboratórios')).toBeTruthy();
  expect(ui.queryByText('Medicamento de referência (ANVISA)')).toBeNull();
  expect(ui.queryByText('Categoria não confirmada')).toBeNull();
});

it('shows the sale type options without the removed explanation and allows only one at a time', async () => {
  const ui = await render(<FiltersScreen />);

  expect(ui.getByText('Venda livre')).toBeTruthy();
  expect(ui.getByText('Com receita')).toBeTruthy();
  expect(ui.getByText('Controle especial')).toBeTruthy();
  expect(ui.getAllByText('Controle especial')).toHaveLength(1);
  expect(ui.queryByText(/sem controle especial/i)).toBeNull();

  await fireEvent.press(ui.getByRole('button', { name: 'Com receita' }));
  await fireEvent.press(ui.getByRole('button', { name: 'Controle especial' }));
  await fireEvent.press(ui.getByRole('button', { name: 'Aplicar filtros' }));

  expect(mockCatalog.applyFilters).toHaveBeenCalledWith([
    expect.objectContaining({ type: 'tipo_venda', value: 'controle_especial' }),
  ]);
});
