import { getConfiguredApiUrl, resolveApiUrl } from '../services/apiConfig';

describe('production API configuration', () => {
  it('uses the Expo manifest URL when Metro does not inline the environment variable', () => {
    const configured = getConfiguredApiUrl(undefined, 'https://precobao.app/backend');
    expect(resolveApiUrl(configured, 'web', false)).toBe('https://precobao.app/backend');
  });

  it('prefers the explicit build environment and preserves the /backend path', () => {
    const configured = getConfiguredApiUrl('https://precobao.app/backend', 'https://other.example/api');
    expect(resolveApiUrl(configured, 'web', false)).toBe('https://precobao.app/backend');
  });
});

describe('resolveApiUrl', () => {
  it('uses the Android emulator host for a local development API', () => {
    expect(resolveApiUrl('http://localhost:8000/', 'android', true)).toBe('http://10.0.2.2:8000');
  });

  it('uses the active private LAN host for physical-device development', () => {
    expect(resolveApiUrl('http://localhost:8000/', 'android', true, '192.168.1.25:8081')).toBe('http://192.168.1.25:8000');
    expect(resolveApiUrl('http://127.0.0.1:8000/', 'ios', true, '192.168.1.25:8081')).toBe('http://192.168.1.25:8000');
  });

  it('does not use a public Expo tunnel hostname as the API host', () => {
    expect(resolveApiUrl('http://localhost:8000/', 'android', true, 'random.exp.direct:8081')).toBe('http://10.0.2.2:8000');
  });

  it('keeps the configured loopback URL for web development', () => {
    expect(resolveApiUrl('http://localhost:8000/', 'web', true)).toBe('http://localhost:8000');
  });

  it('does not default production builds to a local API', () => {
    expect(resolveApiUrl(undefined, 'android', false)).toBe('');
  });

  it('rejects insecure or loopback production API URLs', () => {
    expect(resolveApiUrl('http://api.example.com', 'ios', false)).toBe('');
    expect(resolveApiUrl('https://localhost:8000', 'ios', false)).toBe('');
  });

  it('accepts a public HTTPS endpoint in production', () => {
    expect(resolveApiUrl('https://api.example.com/', 'ios', false)).toBe('https://api.example.com');
    expect(resolveApiUrl('https://api.example.com/', 'android', false)).toBe('https://api.example.com');
  });

  it('rejects credentials embedded in a public API URL', () => {
    expect(resolveApiUrl('https://user:secret@api.example.com', 'android', false)).toBe('');
    expect(resolveApiUrl('https://api.example.com?token=secret', 'android', false)).toBe('');
  });

  it('allows explicit HTTP only for a private Android LAN endpoint in an internal build', () => {
    expect(resolveApiUrl('http://192.168.2.110:8000/', 'android', false, undefined, true)).toBe('http://192.168.2.110:8000');
    expect(resolveApiUrl('http://192.168.2.110:8000/', 'android', false)).toBe('');
    expect(resolveApiUrl('http://api.example.com:8000/', 'android', false, undefined, true)).toBe('');
    expect(resolveApiUrl('http://10.0.2.2:8000/', 'android', false, undefined, true)).toBe('');
    expect(resolveApiUrl('http://192.168.2.110:8000/', 'ios', false, undefined, true)).toBe('');
  });
});
