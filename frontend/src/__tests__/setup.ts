;(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

jest.mock('../services/api', () => ({
  API_URL: 'http://127.0.0.1:8000',
  api: { get: jest.fn(), post: jest.fn(), patch: jest.fn(), delete: jest.fn() },
  setActiveToken: jest.fn(),
  setUnauthorizedCallback: jest.fn(),
  tokenStorage: { get: jest.fn(), set: jest.fn(), delete: jest.fn() },
  guestSessionStorage: { get: jest.fn(), set: jest.fn(), delete: jest.fn() },
}));
