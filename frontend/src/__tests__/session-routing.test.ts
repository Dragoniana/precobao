import { shouldRedirectToAppForActiveSession } from '../services/sessionRouting';

describe('session route restoration', () => {
  it.each(['/login', '/signup', '/forgot-password', '/reset-password'])(
    'keeps public auth route %s available with a restored guest session',
    (pathname) => {
      expect(shouldRedirectToAppForActiveSession(pathname, false, false, true)).toBe(false);
    },
  );

  it.each(['/home', '/favorites', '/profile', '/recovery-code'])(
    'redirects protected route %s to the app for a restored guest session',
    (pathname) => {
      expect(shouldRedirectToAppForActiveSession(pathname, false, false, true)).toBe(true);
    },
  );

  it('waits until session restoration finishes', () => {
    expect(shouldRedirectToAppForActiveSession('/profile', true, true, false)).toBe(false);
  });
});
