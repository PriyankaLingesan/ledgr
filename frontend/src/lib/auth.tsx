/**
 * Authentication state and route guards.
 *
 * The server is the only authority on who the caller is and what they may do.
 * This module asks `/auth/me` for the signed-in identity and its permission
 * list and uses that purely to decide what to *show*; every action is
 * authorized again by the API, so hiding a button here is a convenience, never
 * the control.
 */

import { useQueryClient } from '@tanstack/react-query';
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { Navigate, useLocation } from 'react-router-dom';

import { ApiError, SESSION_ENDED_EVENT, request, setCsrfToken } from './api';
import type { CurrentUser, LoginResponse, Permission, RegisterRequest } from './opsTypes';

type AuthStatus = 'loading' | 'signed-in' | 'signed-out';

interface AuthContextValue {
  status: AuthStatus;
  user: CurrentUser | null;
  /** Why the last session ended, shown once on the login screen. */
  endedReason: 'expired' | 'signed-out' | null;
  login: (email: string, password: string) => Promise<void>;
  register: (payload: RegisterRequest) => Promise<void>;
  logout: () => Promise<void>;
  can: (permission: Permission) => boolean;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<AuthStatus>('loading');
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [endedReason, setEndedReason] = useState<AuthContextValue['endedReason']>(null);

  const clearSession = useCallback(
    (reason: AuthContextValue['endedReason']) => {
      setCsrfToken(null);
      setUser(null);
      setStatus('signed-out');
      setEndedReason(reason);
      // Nothing cached for one identity may be shown to the next.
      queryClient.clear();
    },
    [queryClient],
  );

  // Resume an existing session (page reload): the httpOnly cookie, if any, is sent
  // automatically; the server tells us who we are and hands back the CSRF token.
  useEffect(() => {
    let cancelled = false;
    request<CurrentUser>('/auth/me', { public: true })
      .then((me) => {
        if (cancelled) return;
        setCsrfToken(me.csrf_token);
        setUser(me);
        setStatus('signed-in');
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        // No/expired session is the normal signed-out state, not an error to show.
        if (error instanceof ApiError && error.code === 'session_expired') setEndedReason('expired');
        setStatus('signed-out');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // The server rejected our token mid-session (expired, revoked, deactivated).
  useEffect(() => {
    const onEnded = () => clearSession('expired');
    window.addEventListener(SESSION_ENDED_EVENT, onEnded);
    return () => window.removeEventListener(SESSION_ENDED_EVENT, onEnded);
  }, [clearSession]);

  const acceptLogin = useCallback(
    (response: LoginResponse) => {
      queryClient.clear();
      setCsrfToken(response.csrf_token);
      setUser(response.user);
      setEndedReason(null);
      setStatus('signed-in');
    },
    [queryClient],
  );

  const login = useCallback(
    async (email: string, password: string) => {
      const response = await request<LoginResponse>('/auth/login', {
        method: 'POST',
        body: { email, password },
        public: true,
      });
      acceptLogin(response);
    },
    [acceptLogin],
  );

  const register = useCallback(
    async (payload: RegisterRequest) => {
      const response = await request<LoginResponse>('/auth/register', {
        method: 'POST',
        body: payload,
        public: true,
      });
      acceptLogin(response);
    },
    [acceptLogin],
  );

  const logout = useCallback(async () => {
    try {
      await request<void>('/auth/logout', { method: 'POST' });
    } catch {
      /* the session may already be gone server-side; signing out locally is the point */
    }
    clearSession('signed-out');
  }, [clearSession]);

  const can = useCallback(
    (permission: Permission) => Boolean(user?.permissions.includes(permission)),
    [user],
  );

  const value = useMemo<AuthContextValue>(
    () => ({ status, user, endedReason, login, register, logout, can }),
    [status, user, endedReason, login, register, logout, can],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth must be used inside <AuthProvider>');
  return context;
}

/** Sends signed-out visitors to /login and remembers where they were going. */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  const location = useLocation();

  if (status === 'loading') {
    return (
      <div className="flex min-h-screen items-center justify-center bg-canvas text-sm text-ink-3">
        Checking your session…
      </div>
    );
  }
  if (status === 'signed-out') {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  }
  return <>{children}</>;
}

/** Hides a route's content unless the role grants `permission`. Server still enforces it. */
export function RequirePermission({
  permission,
  children,
}: {
  permission: Permission;
  children: ReactNode;
}) {
  const { can } = useAuth();
  if (!can(permission)) return <Forbidden />;
  return <>{children}</>;
}

/** Renders children only when the signed-in role holds `permission`. */
export function Can({
  permission,
  children,
  fallback = null,
}: {
  permission: Permission;
  children: ReactNode;
  fallback?: ReactNode;
}) {
  const { can } = useAuth();
  return <>{can(permission) ? children : fallback}</>;
}

export function Forbidden() {
  const { user } = useAuth();
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-2 px-6 text-center">
      <p className="text-lg font-semibold text-ink">You don't have access to this page</p>
      <p className="max-w-md text-[0.9375rem] text-ink-2">
        Your role{user ? ` (${user.role.toLowerCase()})` : ''} doesn't include this area. If you
        think that's a mistake, ask an administrator to change your role.
      </p>
    </div>
  );
}
