import { resolveApiUrl } from '../services/apiConfig';

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
  });
});
