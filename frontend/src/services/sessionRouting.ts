const publicSessionRoutes = new Set([
  '/login',
  '/signup',
  '/forgot-password',
  '/reset-password',
]);

export function shouldRedirectToAppForActiveSession(
  pathname: string,
  isRestoringSession: boolean,
  isAuthenticated: boolean,
  hasGuestSession: boolean,
) {
  return !publicSessionRoutes.has(pathname)
    && !isRestoringSession
    && (isAuthenticated || hasGuestSession);
}
