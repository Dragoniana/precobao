import React from 'react';
import { fireEvent, render, waitFor } from '@testing-library/react-native';

import { SortSelector, SortOption } from '../components/SortSelector';

const options: SortOption<'relevancia' | 'menor_preco' | 'maior_preco' | 'nome'>[] = [
  { value: 'relevancia', label: 'Relevância' },
  { value: 'menor_preco', label: 'Menor preço' },
  { value: 'maior_preco', label: 'Maior preço' },
  { value: 'nome', label: 'Nome (A–Z)' },
];

it('opens all sort choices and applies the selection', async () => {
  const onChange = jest.fn();
  const { getByRole, getByText, queryByText } = await render(
    <SortSelector value="relevancia" options={options} onChange={onChange} />,
  );

  await fireEvent.press(getByRole('button', { name: 'Ordenar: Relevância' }));
  expect(getByText('Menor preço')).toBeTruthy();
  expect(getByText('Maior preço')).toBeTruthy();
  expect(getByText('Nome (A–Z)')).toBeTruthy();

  await fireEvent.press(getByText('Menor preço'));
  expect(onChange).toHaveBeenCalledWith('menor_preco');
  await waitFor(() => expect(queryByText('Ordenar por')).toBeNull());
});
