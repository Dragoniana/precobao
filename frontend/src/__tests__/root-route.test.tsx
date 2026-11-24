import React from 'react';
import { render, waitFor } from '@testing-library/react-native';

import Index from '../app/index';

const mockReplace = jest.fn();
const mockSession = {
  hasGuestSession: false,
  isAuthenticated: false,
  isLoading: true,
};
const mockTheme = { isReady: true, palette: { background: '#fff', primary: '#1677ff', text: '#111' } };

jest.mock('expo-router', () => ({ useRouter: () => ({ replace: mockReplace }) }));
jest.mock('../context/AppContext', () => ({ useSessionContext: () => mockSession }));
jest.mock('../context/ThemeContext', () => ({ useAppTheme: () => mockTheme }));

beforeEach(() => {
  mockReplace.mockReset();
  mockSession.hasGuestSession = false;
  mockSession.isAuthenticated = false;
  mockSession.isLoading = true;
  mockTheme.isReady = true;
});

it('shows the branded loader until session restoration finishes', async () => {
  const screen = await render(<Index />);

  expect(screen.getByLabelText('Carregando Preço Bão')).toBeTruthy();
  expect(mockReplace).not.toHaveBeenCalled();

  mockSession.isLoading = false;
  await screen.rerender(<Index />);
  await waitFor(() => expect(mockReplace).toHaveBeenCalledWith('/login'));
});

it('restores authenticated and guest sessions at the root route', async () => {
  mockSession.isLoading = false;
  mockSession.isAuthenticated = true;
  const screen = await render(<Index />);
  await waitFor(() => expect(mockReplace).toHaveBeenCalledWith('/(app)/home'));

  mockReplace.mockReset();
  mockSession.isAuthenticated = false;
  mockSession.hasGuestSession = true;
  await screen.rerender(<Index />);
  await waitFor(() => expect(mockReplace).toHaveBeenCalledWith('/(app)/home'));
});

it('waits for the theme bootstrap before leaving the loader', async () => {
  mockSession.isLoading = false;
  mockTheme.isReady = false;
  await render(<Index />);

  expect(mockReplace).not.toHaveBeenCalled();
});
