import React from 'react';
import { Dimensions, StyleSheet } from 'react-native';
import { act, fireEvent, render, waitFor } from '@testing-library/react-native';

import ForgotPasswordScreen from '../app/forgot-password';
import RecoveryCodeScreen from '../app/recovery-code';
import { api } from '../services/api';
import { darkPalette, lightPalette, spacing } from '../theme';

jest.mock('../components/Screen', () => ({
  Screen: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
let mockIsFocused = true;
jest.mock('expo-router', () => ({
  router: { replace: jest.fn(), push: jest.fn() },
  useIsFocused: () => mockIsFocused,
}));
jest.mock('../context/AppContext', () => ({
  useSessionContext: () => ({ isAuthenticated: true }),
}));
jest.mock('../context/ThemeContext', () => {
  const { StyleSheet: NativeStyleSheet } = jest.requireActual('react-native') as typeof import('react-native');
  const theme = jest.requireActual('../theme') as typeof import('../theme');
  let selectedPalette = 'light';
  return {
    ...jest.requireActual('../context/ThemeContext'),
    useAppTheme: () => ({
      palette: selectedPalette === 'dark' ? theme.darkPalette : theme.lightPalette,
    }),
    createThemedStyles: (factory: (palette: typeof theme.lightPalette) => object) => () =>
      NativeStyleSheet.create(factory(selectedPalette === 'dark' ? theme.darkPalette : theme.lightPalette)),
    setTestPalette: (value: string) => { selectedPalette = value; },
  };
});

const mockedPost = jest.mocked(api.post);

beforeEach(() => {
  mockedPost.mockReset();
  mockIsFocused = true;
});

it('shows reset fields only after a syntactically valid email', async () => {
  const screen = await render(<ForgotPasswordScreen />);

  await fireEvent.changeText(screen.getByTestId('recovery-email'), 'not-an-email');
  await fireEvent.press(screen.getByText('Continuar'));
  expect(screen.getByText('Digite um email válido.')).toBeTruthy();
  expect(screen.queryByTestId('recovery-code')).toBeNull();

  await fireEvent.changeText(screen.getByTestId('recovery-email'), 'account@example.com');
  await fireEvent.press(screen.getByText('Continuar'));
  expect(screen.getByTestId('recovery-code')).toBeTruthy();
  expect(screen.getByTestId('recovery-new-password')).toBeTruthy();
  expect(screen.getByTestId('recovery-confirm-password')).toBeTruthy();
  expect(mockedPost).not.toHaveBeenCalled();
});

it('does not submit mismatched passwords', async () => {
  const screen = await render(<ForgotPasswordScreen />);
  await fireEvent.changeText(screen.getByTestId('recovery-email'), 'account@example.com');
  await fireEvent.press(screen.getByText('Continuar'));
  await fireEvent.changeText(screen.getByTestId('recovery-code'), 'r'.repeat(43));
  await fireEvent.changeText(screen.getByTestId('recovery-new-password'), 'new-password-123');
  await fireEvent.changeText(screen.getByTestId('recovery-confirm-password'), 'different-456');
  await fireEvent.press(screen.getByText('Salvar nova senha'));

  expect(screen.getByText('As senhas não coincidem.')).toBeTruthy();
  expect(mockedPost).not.toHaveBeenCalled();
});

it('submits the recovery code without revealing account existence on errors', async () => {
  mockedPost.mockRejectedValueOnce({ response: { status: 400, data: { detail: 'Código inválido ou expirado.' } } });
  const screen = await render(<ForgotPasswordScreen />);
  await fireEvent.changeText(screen.getByTestId('recovery-email'), 'unknown@example.com');
  await fireEvent.press(screen.getByText('Continuar'));
  await fireEvent.changeText(screen.getByTestId('recovery-code'), 'r'.repeat(43));
  await fireEvent.changeText(screen.getByTestId('recovery-new-password'), 'new-password-123');
  await fireEvent.changeText(screen.getByTestId('recovery-confirm-password'), 'new-password-123');
  await fireEvent.press(screen.getByText('Salvar nova senha'));

  await waitFor(() => expect(mockedPost).toHaveBeenCalledWith(
    '/auth/password-recovery/reset',
    {
      email: 'unknown@example.com',
      code: 'r'.repeat(43),
      new_password: 'new-password-123',
      confirm_new_password: 'new-password-123',
    },
  ));
  expect(await screen.findByText(
    'Não foi possível validar os dados. Confira o email e o código de recuperação.',
  )).toBeTruthy();
});

it('shows a success state and clears password inputs after reset', async () => {
  mockedPost.mockResolvedValueOnce({ data: { message: 'Senha alterada.' } } as any);
  const screen = await render(<ForgotPasswordScreen />);
  await fireEvent.changeText(screen.getByTestId('recovery-email'), 'account@example.com');
  await fireEvent.press(screen.getByText('Continuar'));
  await fireEvent.changeText(screen.getByTestId('recovery-code'), 'r'.repeat(43));
  await fireEvent.changeText(screen.getByTestId('recovery-new-password'), 'new-password-123');
  await fireEvent.changeText(screen.getByTestId('recovery-confirm-password'), 'new-password-123');
  await fireEvent.press(screen.getByText('Salvar nova senha'));

  expect(await screen.findByText('Senha alterada. Entre com sua nova senha.')).toBeTruthy();
  expect(screen.queryByTestId('recovery-new-password')).toBeNull();
  expect(screen.queryByTestId('recovery-code')).toBeNull();
});

it('adapts its card to narrow and wide layouts and both color themes', async () => {
  const themeHarness = jest.requireMock('../context/ThemeContext') as { setTestPalette: (value: string) => void };
  Dimensions.set({
    window: { width: 390, height: 844, scale: 1, fontScale: 1 },
    screen: { width: 390, height: 844, scale: 1, fontScale: 1 },
  });
  const mobile = await render(<ForgotPasswordScreen />);
  const mobileCard = StyleSheet.flatten(mobile.getByTestId('recovery-card').props.style);
  expect(mobileCard.padding).toBe(spacing.xl);
  expect(mobileCard.backgroundColor).toBe(lightPalette.surface);
  await mobile.unmount();

  themeHarness.setTestPalette('dark');
  Dimensions.set({
    window: { width: 1024, height: 900, scale: 1, fontScale: 1 },
    screen: { width: 1024, height: 900, scale: 1, fontScale: 1 },
  });
  const desktopDark = await render(<ForgotPasswordScreen />);
  const darkCard = StyleSheet.flatten(desktopDark.getByTestId('recovery-card').props.style);
  expect(darkCard.padding).toBe(spacing.xxl);
  expect(darkCard.backgroundColor).toBe(darkPalette.surface);
  await desktopDark.unmount();
  themeHarness.setTestPalette('light');
});

it('shows the generated code once and clears it when the screen loses focus', async () => {
  const recoveryCode = 'one-time-code-for-this-test-only-123456';
  mockedPost.mockResolvedValueOnce({
    data: {
      recovery_code: recoveryCode,
      expires_at: '2027-11-18T00:00:00+00:00',
    },
  } as any);
  mockIsFocused = true;
  const screen = await render(<RecoveryCodeScreen />);
  await fireEvent.changeText(screen.getByTestId('recovery-current-password'), 'current-password');
  await fireEvent.press(screen.getByText('Confirmar e gerar código'));

  expect(await screen.findByText(recoveryCode)).toBeTruthy();
  expect(mockedPost).toHaveBeenCalledWith('/auth/password-recovery/codes', {
    current_password: 'current-password',
  });

  await act(async () => {
    mockIsFocused = false;
    screen.rerender(<RecoveryCodeScreen />);
  });
  await waitFor(() => expect(screen.queryByTestId('one-time-recovery-code')).toBeNull());
  mockIsFocused = true;
});
