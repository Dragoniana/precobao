import React from 'react';
import { fireEvent, render } from '@testing-library/react-native';
import { useRouter } from 'expo-router';

import SettingsScreen from '../app/(app)/settings';

jest.mock('expo-router', () => ({ useRouter: jest.fn() }));
jest.mock('../components/Screen', () => {
  const mockReact = jest.requireActual('react');
  const mockReactNative = jest.requireActual('react-native');
  return {
    Screen: ({ children }: { children: React.ReactNode }) =>
      mockReact.createElement(mockReactNative.View, null, children),
  };
});

it('navigates to profile from the available account setting', async () => {
  const push = jest.fn();
  (useRouter as jest.Mock).mockReturnValue({ push });
  const { getByText } = await render(<SettingsScreen />);

  fireEvent.press(getByText('Dados da conta'));
  expect(push).toHaveBeenCalledWith('/(app)/profile');
  expect(getByText(/Apar.ncia/)).toBeTruthy();
  expect(getByText(/tema claro/)).toBeTruthy();
});
