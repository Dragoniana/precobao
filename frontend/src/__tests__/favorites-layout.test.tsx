import React from 'react';
import { Dimensions, StyleSheet } from 'react-native';
import { render } from '@testing-library/react-native';

import FavoritesScreen from '../app/(app)/favorites';
import { Medicamento } from '../types/api';

const mockMedicine = (id: number): Medicamento => ({
  id,
  codigo_barras: '',
  nome: `Medicamento de teste ${id}`,
  name_search: `Medicamento de teste ${id}`,
  principio_ativo: '',
  laboratorio: '',
  exige_receita: false,
  categorias: [],
  imagem_urls: [],
  ofertas: [],
});

jest.mock('../context/AppContext', () => ({
  useCatalogContext: () => ({ markAsViewed: jest.fn() }),
  useFavoritesContext: () => ({
    favoriteMedicines: [mockMedicine(1), mockMedicine(2)],
    favoriteIds: [1, 2],
    toggleFavorite: jest.fn(),
    favoritesLoading: false,
    favoritesError: null,
    refreshFavorites: jest.fn(),
  }),
  useSessionContext: () => ({ sessionMode: 'authenticated' }),
}));
jest.mock('../components/Screen', () => ({ Screen: ({ children }: { children: React.ReactNode }) => <>{children}</> }));
jest.mock('expo-router', () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock('@expo/vector-icons', () => ({ default: () => null }));

it('renders authenticated favorites as a single compact mobile column', async () => {
  Dimensions.set({ window: { width: 390, height: 844, scale: 1, fontScale: 1 }, screen: { width: 390, height: 844, scale: 1, fontScale: 1 } });
  const screen = await render(<FavoritesScreen />);
  const grid = StyleSheet.flatten(screen.getByTestId('favorites-grid').props.style);

  expect(grid.flexDirection).toBe('column');
  expect(grid.flexWrap).toBe('nowrap');
  expect(screen.getByText('2')).toBeTruthy();
  expect(screen.getAllByTestId('medicine-card')).toHaveLength(2);
});

it('keeps the responsive favorites grid on desktop', async () => {
  Dimensions.set({ window: { width: 1440, height: 900, scale: 1, fontScale: 1 }, screen: { width: 1440, height: 900, scale: 1, fontScale: 1 } });
  const screen = await render(<FavoritesScreen />);
  const grid = StyleSheet.flatten(screen.getByTestId('favorites-grid').props.style);

  expect(grid.flexDirection).toBe('row');
  expect(grid.flexWrap).toBe('wrap');
});
