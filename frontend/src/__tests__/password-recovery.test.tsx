import React from 'react';
import { Dimensions, StyleSheet } from 'react-native';
import { fireEvent, render, waitFor } from '@testing-library/react-native';

import ForgotPasswordScreen from '../app/forgot-password';
import ResetPasswordScreen from '../app/reset-password';
import { api } from '../services/api';
import { darkPalette, lightPalette, spacing } from '../theme';

jest.mock('../components/Screen', () => ({ Screen: ({ children }: { children: React.ReactNode }) => <>{children}</> }));
let mockResetToken: string | undefined = 'one-time-reset-token-for-this-test-123456';
jest.mock('expo-router', () => ({
  router: { replace: jest.fn(), push: jest.fn() },
  useLocalSearchParams: () => ({ token: mockResetToken }),
}));
jest.mock('../context/ThemeContext', () => {
  const { StyleSheet: NativeStyleSheet } = jest.requireActual('react-native') as typeof import('react-native');
  const theme = jest.requireActual('../theme') as typeof import('../theme');
  let selectedPalette = 'light';
  return {
    ...jest.requireActual('../context/ThemeContext'),
    useAppTheme: () => ({ palette: selectedPalette === 'dark' ? theme.darkPalette : theme.lightPalette }),
    createThemedStyles: (factory: (palette: typeof theme.lightPalette) => object) => () =>
      NativeStyleSheet.create(factory(selectedPalette === 'dark' ? theme.darkPalette : theme.lightPalette)),
    setTestPalette: (value: string) => { selectedPalette = value; },
  };
});

const mockedPost = jest.mocked(api.post);

beforeEach(() => {
  mockedPost.mockReset();
  mockResetToken = 'one-time-reset-token-for-this-test-123456';
});

it('validates email and shows the generic request result', async () => {
  mockedPost.mockResolvedValueOnce({ data: { message: 'generic' } } as any);
  const screen = await render(<ForgotPasswordScreen />);
  await fireEvent.changeText(screen.getByTestId('recovery-email'), 'not-an-email');
  await fireEvent.press(screen.getByTestId('send-reset-link'));
  expect(screen.getByText('Digite um email válido.')).toBeTruthy();
  expect(mockedPost).not.toHaveBeenCalled();

  await fireEvent.changeText(screen.getByTestId('recovery-email'), 'account@example.com');
  await fireEvent.press(screen.getByTestId('send-reset-link'));
  await waitFor(() => expect(mockedPost).toHaveBeenCalledWith('/auth/password-reset/request', { email: 'account@example.com' }));
  expect(await screen.findByText(/Se o endereço estiver cadastrado/)).toBeTruthy();
  expect(screen.queryByTestId('recovery-code')).toBeNull();
});

it('shows rate limiting without disclosing account state', async () => {
  mockedPost.mockRejectedValueOnce({ response: { status: 429 } });
  const screen = await render(<ForgotPasswordScreen />);
  await fireEvent.changeText(screen.getByTestId('recovery-email'), 'unknown@example.org');
  await fireEvent.press(screen.getByTestId('send-reset-link'));
  expect(await screen.findByText('Muitas solicitações. Aguarde antes de tentar novamente.')).toBeTruthy();
});

it('does not submit different passwords', async () => {
  const screen = await render(<ResetPasswordScreen />);
  await fireEvent.changeText(screen.getByTestId('reset-new-password'), 'new-password-123');
  await fireEvent.changeText(screen.getByTestId('reset-confirm-password'), 'different-456');
  await fireEvent.press(screen.getByTestId('save-reset-password'));
  expect(screen.getByText('As senhas não coincidem.')).toBeTruthy();
  expect(mockedPost).not.toHaveBeenCalled();
});

it('submits the email token once and returns to login after success', async () => {
  mockedPost.mockResolvedValueOnce({ data: { message: 'Senha alterada.' } } as any);
  const screen = await render(<ResetPasswordScreen />);
  await fireEvent.changeText(screen.getByTestId('reset-new-password'), 'new-password-123');
  await fireEvent.changeText(screen.getByTestId('reset-confirm-password'), 'new-password-123');
  await fireEvent.press(screen.getByTestId('save-reset-password'));

  await waitFor(() => expect(mockedPost).toHaveBeenCalledWith('/auth/password-reset/confirm', {
    token: 'one-time-reset-token-for-this-test-123456',
    new_password: 'new-password-123',
    confirm_new_password: 'new-password-123',
  }));
  expect(await screen.findByText('Senha alterada. Entre com sua nova senha.')).toBeTruthy();
  expect(screen.queryByTestId('reset-new-password')).toBeNull();
});

it('reports expired links without echoing the token', async () => {
  mockedPost.mockRejectedValueOnce({ response: { status: 400 } });
  const screen = await render(<ResetPasswordScreen />);
  await fireEvent.changeText(screen.getByTestId('reset-new-password'), 'new-password-123');
  await fireEvent.changeText(screen.getByTestId('reset-confirm-password'), 'new-password-123');
  await fireEvent.press(screen.getByTestId('save-reset-password'));
  expect(await screen.findByText('Este link não é válido ou expirou. Solicite um novo link.')).toBeTruthy();
  expect(screen.queryByText(/one-time-reset-token/)).toBeNull();
});

it('preserves responsive card sizes and both themes', async () => {
  const themeHarness = jest.requireMock('../context/ThemeContext') as { setTestPalette: (value: string) => void };
  Dimensions.set({ window: { width: 390, height: 844, scale: 1, fontScale: 1 }, screen: { width: 390, height: 844, scale: 1, fontScale: 1 } });
  const mobile = await render(<ForgotPasswordScreen />);
  const mobileCard = StyleSheet.flatten(mobile.getByTestId('recovery-card').props.style);
  expect(mobileCard.padding).toBe(spacing.xl);
  expect(mobileCard.backgroundColor).toBe(lightPalette.surface);
  await mobile.unmount();

  themeHarness.setTestPalette('dark');
  Dimensions.set({ window: { width: 768, height: 900, scale: 1, fontScale: 1 }, screen: { width: 768, height: 900, scale: 1, fontScale: 1 } });
  const tablet = await render(<ForgotPasswordScreen />);
  const tabletCard = StyleSheet.flatten(tablet.getByTestId('recovery-card').props.style);
  expect(tabletCard.maxWidth).toBe(520);
  expect(tabletCard.backgroundColor).toBe(darkPalette.surface);
  await tablet.unmount();

  Dimensions.set({ window: { width: 1440, height: 900, scale: 1, fontScale: 1 }, screen: { width: 1440, height: 900, scale: 1, fontScale: 1 } });
  const desktop = await render(<ResetPasswordScreen />);
  const desktopCard = StyleSheet.flatten(desktop.getByTestId('recovery-card')?.props.style ?? {});
  expect(desktopCard.backgroundColor).toBe(darkPalette.surface);
  expect(desktopCard.padding).toBe(spacing.xl);
  await desktop.unmount();
  themeHarness.setTestPalette('light');
});
