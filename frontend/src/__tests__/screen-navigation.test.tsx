import React from 'react';
import { fireEvent, render } from '@testing-library/react-native';
import { router, usePathname } from 'expo-router';

import { Screen } from '../components/Screen';

jest.mock('expo-router', () => ({
  router: { push: jest.fn(), replace: jest.fn() },
  usePathname: jest.fn(),
}));
jest.mock('../context/AppContext', () => ({
  useSessionContext: () => ({ sessionMode: 'guest', currentUserName: '', logout: jest.fn() }),
}));
jest.mock('react-native-safe-area-context', () => ({ useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }) }));

it('shows four bottom destinations and leaves every one unselected on the Search route', async () => {
  (usePathname as jest.Mock).mockReturnValue('/search');
  const { getByLabelText, queryByLabelText } = await render(<Screen><></></Screen>);

  expect(getByLabelText('Abrir início').props.accessibilityState.selected).toBe(false);
  expect(getByLabelText('Abrir favoritos').props.accessibilityState.selected).toBe(false);
  expect(getByLabelText('Abrir perfil').props.accessibilityState.selected).toBe(false);
  expect(getByLabelText('Abrir menu')).toBeTruthy();
  expect(queryByLabelText('Abrir buscar')).toBeNull();

  fireEvent.press(getByLabelText('Abrir favoritos'));
  expect(router.push).toHaveBeenCalledWith('/favorites');
});
