import { create } from 'axios';
import * as SecureStore from 'expo-secure-store';
import { Platform } from 'react-native';

export const API_URL = process.env.EXPO_PUBLIC_API_URL || 'http://127.0.0.1:8000';
export const TOKEN_KEY = 'auth_token';

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

let unauthorizedCallback: (() => void) | null = null;
let activeToken: string | null | undefined;

export const setActiveToken = (token: string | null) => {
  activeToken = token;
};

export const setUnauthorizedCallback = (callback: () => void) => {
  unauthorizedCallback = callback;
};

export const api = create({
  baseURL: API_URL,
});

api.interceptors.request.use(
  async (config) => {
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
