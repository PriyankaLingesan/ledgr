import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError, SESSION_ENDED_EVENT, request, setCsrfToken } from './api';
import { canManageRole } from './opsTypes';

function mockFetch(status: number, body: unknown) {
  const fn = vi.fn(async (_url: string, _init?: RequestInit) =>
    new Response(status === 204 ? null : JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json', 'X-Request-ID': 'req-1' },
    }),
  );
  vi.stubGlobal('fetch', fn);
  return fn;
}

describe('api client', () => {
  beforeEach(() => setCsrfToken(null));
  afterEach(() => vi.unstubAllGlobals());

  it('sends the httpOnly cookie and never an Authorization header', async () => {
    const fetchMock = mockFetch(200, { ok: true });
    await request('/accounts');
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.credentials).toBe('include');
    expect((init.headers as Record<string, string>).Authorization).toBeUndefined();
  });

  it('adds the CSRF token to state-changing requests only', async () => {
    setCsrfToken('csrf-123');
    const fetchMock = mockFetch(200, {});
    await request('/accounts');
    await request('/accounts', { method: 'POST', body: { code: 'X' } });
    const get = fetchMock.mock.calls[0][1] as RequestInit;
    const post = fetchMock.mock.calls[1][1] as RequestInit;
    expect((get.headers as Record<string, string>)['X-CSRF-Token']).toBeUndefined();
    expect((post.headers as Record<string, string>)['X-CSRF-Token']).toBe('csrf-123');
  });

  it('does not attach the CSRF token to public (login/register) calls', async () => {
    setCsrfToken('csrf-123');
    const fetchMock = mockFetch(200, {});
    await request('/auth/login', { method: 'POST', body: {}, public: true });
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>)['X-CSRF-Token']).toBeUndefined();
  });

  it('surfaces the server error shape verbatim', async () => {
    mockFetch(422, { error: { code: 'unbalanced_transaction', message: 'debits must equal credits', details: { d: 1 }, request_id: 'r1' } });
    await expect(request('/transactions', { method: 'POST', body: {} })).rejects.toMatchObject({
      status: 422,
      code: 'unbalanced_transaction',
      message: 'debits must equal credits',
      requestId: 'r1',
    });
  });

  it('announces an ended session on a 401 - but not for a failed login', async () => {
    const listener = vi.fn();
    window.addEventListener(SESSION_ENDED_EVENT, listener);
    mockFetch(401, { error: { code: 'session_expired', message: 'expired', details: {}, request_id: null } });

    await expect(request('/accounts')).rejects.toBeInstanceOf(ApiError);
    expect(listener).toHaveBeenCalledTimes(1);

    await expect(request('/auth/login', { method: 'POST', body: {}, public: true })).rejects.toBeInstanceOf(ApiError);
    expect(listener).toHaveBeenCalledTimes(1); // unchanged: a wrong password is not an ended session
    window.removeEventListener(SESSION_ENDED_EVENT, listener);
  });

  it('reports an unreachable API as a network_error', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new TypeError('failed to fetch'))));
    await expect(request('/accounts')).rejects.toMatchObject({ status: 0, code: 'network_error' });
  });
});

describe('role hierarchy (mirrors the server rule)', () => {
  it('lets an owner manage every role', () => {
    for (const role of ['OWNER', 'ADMIN', 'FINANCE', 'OPERATIONS', 'AUDITOR'] as const) {
      expect(canManageRole('OWNER', role)).toBe(true);
    }
  });

  it('lets an admin manage only roles below admin', () => {
    expect(canManageRole('ADMIN', 'FINANCE')).toBe(true);
    expect(canManageRole('ADMIN', 'AUDITOR')).toBe(true);
    expect(canManageRole('ADMIN', 'ADMIN')).toBe(false);
    expect(canManageRole('ADMIN', 'OWNER')).toBe(false);
  });

  it('gives operators and auditors no management rights', () => {
    expect(canManageRole('FINANCE', 'AUDITOR')).toBe(false);
    expect(canManageRole('AUDITOR', 'AUDITOR')).toBe(false);
    expect(canManageRole(undefined, 'AUDITOR')).toBe(false);
  });
});

describe('api client with a non-JSON response', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('reports a gateway error page as an ApiError, not a raw SyntaxError', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response('<html><body>502 Bad Gateway</body></html>', {
          status: 502,
          headers: { 'Content-Type': 'text/html', 'X-Request-ID': 'req-502' },
        }),
      ),
    );
    const failure = await request('/accounts').catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(ApiError);
    expect((failure as ApiError).status).toBe(502);
    expect((failure as ApiError).requestId).toBe('req-502');
  });
});
