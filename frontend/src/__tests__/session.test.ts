import { act, renderHook, waitFor } from '@testing-library/react-native';

import { api, tokenStorage } from '../services/api';
import { useAuthSession } from '../hooks/useAuthSession';

const user = {
  id: 'u-1', email: 'ana@example.com', nome: 'Ana Teste',
  is_active: true, is_superuser: false, is_verified: true,
};

beforeEach(() => {
  jest.clearAllMocks();
  (tokenStorage.get as jest.Mock).mockResolvedValue(null);
  (tokenStorage.set as jest.Mock).mockResolvedValue(undefined);
  (tokenStorage.delete as jest.Mock).mockResolvedValue(undefined);
});

it('only completes login after validating the token and supports persistent logout', async () => {
  (api.post as jest.Mock).mockResolvedValue({ data: { access_token: 'token-ana' } });
  (api.get as jest.Mock).mockResolvedValue({ data: user });
  const { result } = await renderHook(() => useAuthSession());

  await waitFor(() => expect(result.current.isLoading).toBe(false));
  let login!: { ok: boolean };
  await act(async () => { login = await result.current.signIn(user.email, 'secret'); });
  expect(login.ok).toBe(true);
  expect(result.current.isAuthenticated).toBe(true);
  expect(result.current.user?.id).toBe('u-1');
  expect(tokenStorage.set).toHaveBeenCalledWith('token-ana');

  await act(async () => { await result.current.logout(); });
  expect(result.current.isAuthenticated).toBe(false);
  expect(result.current.user).toBeNull();
  expect(tokenStorage.delete).toHaveBeenCalled();
});

it('restores only a token validated by the backend', async () => {
  (tokenStorage.get as jest.Mock).mockResolvedValue('stored-token');
  (api.get as jest.Mock).mockResolvedValue({ data: user });
  const { result } = await renderHook(() => useAuthSession());
  await waitFor(() => expect(result.current.isAuthenticated).toBe(true));
  expect(result.current.token).toBe('stored-token');

  jest.clearAllMocks();
  (tokenStorage.get as jest.Mock).mockResolvedValue('expired-token');
  (api.get as jest.Mock).mockRejectedValue({ response: { status: 401, data: {} } });
  const expired = await renderHook(() => useAuthSession());
  await waitFor(() => expect(expired.result.current.isLoading).toBe(false));
  expect(expired.result.current.isAuthenticated).toBe(false);
  expect(tokenStorage.delete).toHaveBeenCalled();
});

it('clears a newly issued token when the account validation request fails', async () => {
  (api.post as jest.Mock).mockResolvedValue({ data: { access_token: 'unverified-token' } });
  (api.get as jest.Mock).mockRejectedValue({ response: { status: 401, data: {} } });
  const { result } = await renderHook(() => useAuthSession());
  await waitFor(() => expect(result.current.isLoading).toBe(false));

  let response!: { ok: boolean };
  await act(async () => { response = await result.current.signIn(user.email, 'secret'); });
  expect(response.ok).toBe(false);
  expect(result.current.isAuthenticated).toBe(false);
  expect(result.current.token).toBeNull();
  expect(tokenStorage.delete).toHaveBeenCalled();
});

it('persists profile edits and reports backend conflicts without changing local data', async () => {
  (tokenStorage.get as jest.Mock).mockResolvedValue('valid-token');
  (api.get as jest.Mock).mockResolvedValue({ data: user });
  (api.patch as jest.Mock).mockResolvedValue({
    data: { ...user, nome: 'Ana Nova', email: 'ana.nova@example.com' },
  });
  const { result } = await renderHook(() => useAuthSession());
  await waitFor(() => expect(result.current.isAuthenticated).toBe(true));
  let response!: { ok: boolean };
  await act(async () => {
    response = await result.current.updateProfile('Ana Nova', 'ana.nova@example.com');
  });
  expect(response.ok).toBe(true);
  expect(api.patch).toHaveBeenCalledWith('/users/me', {
    nome: 'Ana Nova', email: 'ana.nova@example.com',
  });
  expect(result.current.currentUserEmail).toBe('ana.nova@example.com');

  (api.patch as jest.Mock).mockRejectedValue({
    response: { data: { detail: 'Email já cadastrado' } },
  });
  await act(async () => {
    response = await result.current.updateProfile('Ana', 'outra@example.com');
  });
  expect(response.ok).toBe(false);
  expect(result.current.currentUserEmail).toBe('ana.nova@example.com');
});
