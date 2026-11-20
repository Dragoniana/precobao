import React from 'react';
import { fireEvent, render } from '@testing-library/react-native';

import { CatalogFiltersSidebar } from '../components/CatalogFiltersSidebar';
import { FilterOption } from '../context/AppContext';
import { api } from '../services/api';

const mockCatalog = {
  selectedFilters: [] as FilterOption[],
  filterOptions: [
    { id: 'tipo_venda:venda_livre', type: 'tipo_venda', label: 'Venda livre', value: 'venda_livre', count: 0 },
    { id: 'tipo_venda:com_receita', type: 'tipo_venda', label: 'Com receita', value: 'com_receita', count: 4 },
    { id: 'tipo_venda:controle_especial', type: 'tipo_venda', label: 'Controle especial', value: 'controle_especial', count: 2 },
    { id: 'categoria:similar', type: 'categoria', label: 'Similar', value: 'Similar' },
  ] as FilterOption[],
  applyFilters: jest.fn(),
  clearFilters: jest.fn(),
  selectedPharmacyId: null,
  setSelectedPharmacyId: jest.fn(),
};

jest.mock('../context/AppContext', () => ({ useCatalogContext: () => mockCatalog }));

beforeEach(() => {
  jest.clearAllMocks();
  (api.get as jest.Mock).mockResolvedValue({ data: [] });
});

it('shows every sale type and switches the exclusive selection in the desktop sidebar', async () => {
  const ui = await render(<CatalogFiltersSidebar />);

  expect(ui.getByText('Tipo de venda')).toBeTruthy();
  expect(ui.getByText('Venda livre')).toBeTruthy();
  expect(ui.getByText('Com receita')).toBeTruthy();
  expect(ui.getByText('Controle especial')).toBeTruthy();
  expect(ui.getByText(/sem controle especial/i)).toBeTruthy();

  await fireEvent.press(ui.getByRole('button', { name: 'Com receita' }));
  await fireEvent.press(ui.getByRole('button', { name: 'Venda livre' }));
  await fireEvent.press(ui.getByRole('button', { name: 'Aplicar filtros' }));

  expect(mockCatalog.applyFilters).toHaveBeenCalledWith([
    expect.objectContaining({ type: 'tipo_venda', value: 'venda_livre' }),
  ]);
});
