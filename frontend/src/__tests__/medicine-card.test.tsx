import React from 'react';
import { Dimensions, StyleSheet } from 'react-native';
import { fireEvent, render } from '@testing-library/react-native';

import { MedicineCard } from '../components/MedicineCard';
import { Medicamento } from '../types/api';

const mockToggleFavorite = jest.fn();
let mockFavoriteIds: number[] = [];
let mockSessionMode: 'authenticated' | 'guest' = 'authenticated';

jest.mock('../context/AppContext', () => ({
  useFavoritesContext: () => ({ favoriteIds: mockFavoriteIds, toggleFavorite: mockToggleFavorite }),
  useSessionContext: () => ({ sessionMode: mockSessionMode }),
}));

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));

const medicine: Medicamento = {
  id: 42,
  codigo_barras: '',
  nome: 'Dipirona 500 mg caixa com 20 comprimidos',
  name_search: 'dipirona',
  principio_ativo: 'Dipirona',
  laboratorio: 'Laboratorio Exemplo',
  exige_receita: false,
  categorias: [],
  ofertas: [{ id: 1, preco: 12.5, disponivel: true, farmacia_nome: 'Farmacia Central' } as never],
};

beforeEach(() => {
  jest.clearAllMocks();
  mockFavoriteIds = [];
  mockSessionMode = 'authenticated';
});

it('opens the medicine details when the image, title, price, or open card surface is pressed', async () => {
  const onPress = jest.fn();
  const { getByTestId, getByText } = await render(<MedicineCard medicine={medicine} onPress={onPress} compact />);

  await fireEvent.press(getByText(/Imagem/));
  await fireEvent.press(getByTestId('medicine-card-title'));
  await fireEvent.press(getByTestId('medicine-card-price'));
  await fireEvent.press(getByTestId('medicine-card'));

  expect(onPress).toHaveBeenCalledTimes(4);
});

it('applies and removes the blue hover treatment on the whole card', async () => {
  const { getByTestId } = await render(<MedicineCard medicine={medicine} onPress={jest.fn()} />);
  const card = getByTestId('medicine-card');
  expect(StyleSheet.flatten(card.props.style).borderColor).not.toBe('#1688F6');

  await fireEvent(card, 'hoverIn');
  expect(StyleSheet.flatten(getByTestId('medicine-card').props.style).borderColor).toBe('#1688F6');

  await fireEvent(getByTestId('medicine-card'), 'hoverOut');
  expect(StyleSheet.flatten(getByTestId('medicine-card').props.style).borderColor).not.toBe('#1688F6');
});

it('keeps the favorite button independent from card navigation and persists the toggle', async () => {
  const onPress = jest.fn();
  const { getByLabelText } = await render(<MedicineCard medicine={medicine} onPress={onPress} />);

  await fireEvent.press(getByLabelText(/Salvar Dipirona/));
  expect(mockToggleFavorite).toHaveBeenCalledWith(42);
  expect(onPress).not.toHaveBeenCalled();
});

it('opens comparison exactly once from the comparison action', async () => {
  const onPress = jest.fn();
  const { getByTestId } = await render(<MedicineCard medicine={medicine} onPress={onPress} />);

  await fireEvent.press(getByTestId('medicine-card-compare'));
  expect(onPress).toHaveBeenCalledTimes(1);
});

it('exposes a labeled card group and independent actions with a visible focus state', async () => {
  const { getByTestId, getByLabelText } = await render(<MedicineCard medicine={medicine} onPress={jest.fn()} />);
  expect(getByTestId('medicine-card').props.role).toBe('group');
  expect(getByTestId('medicine-card').props.accessibilityLabel).toMatch(/Abrir detalhes de Dipirona/);
  expect(getByLabelText(/Salvar Dipirona/).props.accessibilityRole).toBe('button');
  expect(getByTestId('medicine-card-compare').props.accessibilityRole).toBe('button');

  await fireEvent(getByTestId('medicine-card'), 'focus');
  expect(StyleSheet.flatten(getByTestId('medicine-card').props.style).borderColor).toBe('#1688F6');
});

it('keeps compact card controls and prices inside a very narrow mobile layout', async () => {
  Dimensions.set({ window: { width: 320, height: 720, scale: 1, fontScale: 1 }, screen: { width: 320, height: 720, scale: 1, fontScale: 1 } });
  mockSessionMode = 'guest';
  const onPress = jest.fn();
  const { getByTestId, getByText, getByLabelText } = await render(<MedicineCard medicine={medicine} onPress={onPress} compact />);

  await fireEvent.press(getByText(/Imagem/));
  await fireEvent.press(getByLabelText(/Salvar Dipirona/));
  expect(onPress).toHaveBeenCalledTimes(1);
  expect(mockToggleFavorite).not.toHaveBeenCalled();
  expect(StyleSheet.flatten(getByTestId('medicine-card').props.style).width).toBe('100%');
  expect(StyleSheet.flatten(getByTestId('medicine-card-favorite').props.style).left).toBe(48);
  expect(StyleSheet.flatten(getByTestId('medicine-card-price-section').props.style).flexDirection).toBe('column');
  expect(StyleSheet.flatten(getByTestId('medicine-card-price-column').props.style).width).toBe('100%');
  expect(StyleSheet.flatten(getByTestId('medicine-card-compare').props.style).minHeight).toBe(44);
});

it('shows a friendly laboratory name while keeping the medicine source value unchanged', async () => {
  const sourceValue = '05161069000110 - BRAINFARMA INDÚSTRIA QUÍMICA E FARMACÊUTICA S.A.';
  const { getByText } = await render(<MedicineCard medicine={{ ...medicine, laboratorio: sourceValue }} onPress={jest.fn()} />);

  expect(getByText('Brainfarma Indústria Química e Farmacêutica S.A.')).toBeTruthy();
  expect(sourceValue).toContain('05161069000110');
});
