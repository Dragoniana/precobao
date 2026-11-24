import { create } from 'axios';
import Constants from 'expo-constants';
import * as SecureStore from 'expo-secure-store';
import { Platform } from 'react-native';

import { getConfiguredApiUrl, resolveApiUrl } from './apiConfig';

export const API_URL = resolveApiUrl(
  getConfiguredApiUrl(
    process.env.EXPO_PUBLIC_API_URL,
    Constants.expoConfig?.extra?.apiUrl,
  ),
  Platform.OS,
  __DEV__,
  Constants.expoConfig?.hostUri,
  process.env.EXPO_PUBLIC_ALLOW_INSECURE_LAN_API === 'true',
);
export const TOKEN_KEY = 'auth_token';
export const GUEST_SESSION_KEY = 'guest_session';

/**
 * Platform-aware storage engine to prevent runtime crashes on the Web platform.
 * Falls back to standard localStorage when running inside browsers.
 */
export const tokenStorage = {
  get: async (): Promise<string | null> => {
    if (Platform.OS === 'web') {
      return localStorage.getItem(TOKEN_KEY);
    }
    return SecureStore.getItemAsync(TOKEN_KEY);
  },
  set: async (value: string): Promise<void> => {
    if (Platform.OS === 'web') {
      localStorage.setItem(TOKEN_KEY, value);
      return;
    }
    return SecureStore.setItemAsync(TOKEN_KEY, value);
  },
  delete: async (): Promise<void> => {
    if (Platform.OS === 'web') {
      localStorage.removeItem(TOKEN_KEY);
      return;
    }
    return SecureStore.deleteItemAsync(TOKEN_KEY);
  },
};

export const guestSessionStorage = {
  get: async (): Promise<boolean> => {
    if (Platform.OS === 'web') return localStorage.getItem(GUEST_SESSION_KEY) === 'true';
    return (await SecureStore.getItemAsync(GUEST_SESSION_KEY)) === 'true';
  },
  set: async (): Promise<void> => {
    if (Platform.OS === 'web') {
      localStorage.setItem(GUEST_SESSION_KEY, 'true');
      return;
    }
    return SecureStore.setItemAsync(GUEST_SESSION_KEY, 'true');
  },
  delete: async (): Promise<void> => {
    if (Platform.OS === 'web') {
      localStorage.removeItem(GUEST_SESSION_KEY);
      return;
    }
    return SecureStore.deleteItemAsync(GUEST_SESSION_KEY);
  },
};

let unauthorizedCallback: (() => void) | null = null;
let activeToken: string | null | undefined;

export const setActiveToken = (token: string | null) => {
  activeToken = token;
};

export const setUnauthorizedCallback = (callback: () => void) => {
  unauthorizedCallback = callback;
};

export const api = create({
  baseURL: API_URL || undefined,
  timeout: 15_000,
});

api.interceptors.request.use(
  async (config) => {
    if (Platform.OS !== 'web' && !API_URL) {
      throw new Error('Configure EXPO_PUBLIC_API_URL com uma URL HTTPS da API para este aplicativo.');
    }
    try {
      const token = activeToken === undefined ? await tokenStorage.get() : activeToken;
      if (token) {
        config.headers.Authorization = `Bearer ${token}`;
      }
    } catch (error) {
      console.error('Error fetching token from storage engine', error);
    }
    return config;
  },
  (error) => {
    return Promise.reject(error);
  }
);

api.interceptors.response.use(
  (response) => response,
  (error) => {
    const sentToken = error.config?.headers?.Authorization;
    if (error.response?.status === 401 &&
        !error.config?.url?.includes('/auth/jwt/login') && sentToken &&
        sentToken === `Bearer ${activeToken}`) {
      if (unauthorizedCallback) {
        unauthorizedCallback();
      }
    }
    return Promise.reject(error);
  }
);
