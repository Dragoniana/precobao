import { useCallback, useEffect, useRef, useState } from 'react';

import { api, setActiveToken, setUnauthorizedCallback, tokenStorage } from '../services/api';

export type User = {
  id: string;
  email: string;
  nome?: string;
  name?: string;
  is_active: boolean;
  is_superuser: boolean;
  is_verified: boolean;
};
export type LoginResult = { ok: boolean; message: string };
export type RegisterPayload = { name: string; email: string; password: string; confirmPassword: string };
export type RegisterResult = { ok: boolean; message: string };
export type SessionMode = 'guest' | 'authenticated';

function apiMessage(value: unknown, fallback: string): string {
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) return value.map((item: any) => item?.msg || item?.message || '').filter(Boolean).join('\n') || fallback;
  if (value && typeof value === 'object' && 'msg' in value) return String((value as { msg: unknown }).msg);
  if (value && typeof value === 'object' && 'message' in value) return String((value as { message: unknown }).message);
  return fallback;
}

export function useAuthSession() {
  const [isLoading, setIsLoading] = useState(true);
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [token, setToken] = useState<string | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [sessionMode, setSessionMode] = useState<SessionMode>('guest');
  const [sessionVersion, setSessionVersion] = useState(0);
  const [sessionRestoreError, setSessionRestoreError] = useState<string | null>(null);
  const [currentUserName, setCurrentUserName] = useState<string | null>(null);
  const [currentUserEmail, setCurrentUserEmail] = useState<string | null>(null);
  const epoch = useRef(0);
  const restoreRequestId = useRef(0);

  const clearSession = useCallback(async () => {
    epoch.current += 1;
    restoreRequestId.current += 1;
    setIsLoading(false);
    setSessionVersion(epoch.current);
    setActiveToken(null);
    setIsAuthenticated(false);
    setToken(null);
    setUser(null);
    setSessionMode('guest');
    setCurrentUserName(null);
    setCurrentUserEmail(null);
    setSessionRestoreError(null);
    try {
      await tokenStorage.delete();
    } catch {
      // The in-memory session is already cleared; keep logout deterministic.
    }
  }, []);

  const logout = clearSession;

  useEffect(() => {
    setUnauthorizedCallback(() => { void clearSession(); });
  }, [clearSession]);

  const retrySessionRestore = useCallback(async () => {
    const restoreId = ++restoreRequestId.current;
    const requestEpoch = epoch.current;
    let storedToken: string | null = null;
    setIsLoading(true);
    setSessionRestoreError(null);
    try {
      storedToken = await tokenStorage.get();
      if (!storedToken || restoreId !== restoreRequestId.current || requestEpoch !== epoch.current) return false;
      setActiveToken(storedToken);
      const { data } = await api.get<User>('/users/me');
      if (restoreId !== restoreRequestId.current || requestEpoch !== epoch.current) return false;
      epoch.current += 1;
      setSessionVersion(epoch.current);
      setUser(data);
      setToken(storedToken);
      setIsAuthenticated(true);
      setSessionMode('authenticated');
      setCurrentUserName(data.nome || data.name || data.email);
      setCurrentUserEmail(data.email);
      setSessionRestoreError(null);
      return true;
    } catch (error: any) {
      if (restoreId !== restoreRequestId.current || requestEpoch !== epoch.current) return false;
      if (error.response?.status === 401) {
        await clearSession();
      } else {
        // A timeout, server error, or SecureStore error does not prove that a
        // saved token is invalid. Keep it for an explicit retry when online.
        setActiveToken(null);
        setIsAuthenticated(false);
        setToken(null);
        setUser(null);
        setSessionMode('guest');
        setSessionRestoreError(storedToken
          ? 'Não foi possível validar sua sessão agora. Ela continua salva neste aparelho; verifique a conexão e tente novamente.'
          : 'Não foi possível acessar o armazenamento seguro. Os dados de sessão não foram removidos.');
      }
      return false;
    } finally {
      if (restoreId === restoreRequestId.current) setIsLoading(false);
    }
  }, [clearSession]);

  useEffect(() => {
    void retrySessionRestore();
    return () => { restoreRequestId.current += 1; };
  }, [retrySessionRestore]);

  const continueAsGuest = useCallback(() => { void clearSession(); }, [clearSession]);

  const signIn = useCallback(async (email: string, password: string): Promise<LoginResult> => {
    const startingEpoch = epoch.current;
    let authEpoch: number | null = null;
    let tokenWasStored = false;
    setSessionRestoreError(null);
    try {
      const formData = new URLSearchParams({ username: email.trim(), password });
      const { data } = await api.post('/auth/jwt/login', formData.toString(), {
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      });
      if (startingEpoch !== epoch.current) return { ok: false, message: 'Sessão alterada.' };
      if (!data.access_token) return { ok: false, message: 'O servidor não retornou um token de acesso.' };
      const accessToken: string = data.access_token;
      authEpoch = ++epoch.current;
      setSessionVersion(authEpoch);
      setActiveToken(accessToken);
      await tokenStorage.set(accessToken);
      tokenWasStored = true;
      const { data: verifiedUser } = await api.get<User>('/users/me');
      if (authEpoch !== epoch.current) return { ok: false, message: 'Sessão alterada.' };
      setUser(verifiedUser);
      setToken(accessToken);
      setIsAuthenticated(true);
      setSessionMode('authenticated');
      setCurrentUserName(verifiedUser.nome || verifiedUser.name || verifiedUser.email);
      setCurrentUserEmail(verifiedUser.email);
      setSessionRestoreError(null);
      return { ok: true, message: 'Login realizado com sucesso.' };
    } catch (error: any) {
      if (authEpoch !== null && authEpoch === epoch.current) {
        if (error.response?.status === 401) {
          await clearSession();
        } else {
          setActiveToken(null);
          setIsAuthenticated(false);
          setToken(null);
          setUser(null);
          setSessionMode('guest');
          setSessionRestoreError('O servidor emitiu uma sessão, mas não foi possível validá-la agora. A sessão foi mantida neste aparelho; verifique a conexão e tente novamente.');
          if (!tokenWasStored) {
            setSessionRestoreError('Não foi possível salvar ou validar a sessão neste aparelho. Verifique a conexão e tente entrar novamente.');
          }
        }
      }
      return {
        ok: false,
        message: error.response
          ? apiMessage(error.response.data?.detail, 'Não foi possível entrar.')
          : 'Erro de conexão com o servidor de autenticação.',
      };
    }
  }, [clearSession]);

  const registerUser = useCallback(async (payload: RegisterPayload): Promise<RegisterResult> => {
    try {
      await api.post('/auth/register', {
        nome: payload.name.trim(),
        email: payload.email.trim(),
        password: payload.password,
      });
      return { ok: true, message: 'Cadastro realizado com sucesso.' };
    } catch (error: any) {
      return {
        ok: false,
        message: error.response
          ? apiMessage(error.response.data?.detail, 'Não foi possível concluir o cadastro.')
          : 'Erro de conexão com o servidor de cadastro.',
      };
    }
  }, []);

  const updateProfile = useCallback(async (name: string, email: string): Promise<LoginResult> => {
    if (!isAuthenticated) return { ok: false, message: 'Entre na conta para editar o perfil.' };
    const requestEpoch = epoch.current;
    try {
      const { data } = await api.patch<User>('/users/me', { nome: name.trim(), email: email.trim() });
      if (requestEpoch !== epoch.current) return { ok: false, message: 'Sessão alterada.' };
      setUser(data);
      setCurrentUserName(data.nome || data.email);
      setCurrentUserEmail(data.email);
      return { ok: true, message: 'Perfil atualizado.' };
    } catch (error: any) {
      return { ok: false, message: apiMessage(error.response?.data?.detail, 'Não foi possível salvar o perfil.') };
    }
  }, [isAuthenticated]);

  return {
    isLoading, isAuthenticated, token, user, sessionMode, sessionVersion, sessionRestoreError,
    currentUserName, currentUserEmail, clearSession, logout, continueAsGuest,
    retrySessionRestore, signIn, registerUser, updateProfile,
  };
}
