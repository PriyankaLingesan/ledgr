/**
 * Thin API client.
 *
 * The server speaks one error shape for every failure, so the client surfaces
 * that shape verbatim - screens show the ledger's own reason (`unbalanced_
 * transaction`, `insufficient_funds`) rather than a generic "something went
 * wrong".
 */

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api/v1';

export interface ApiErrorBody {
  code: string;
  message: string;
  details: Record<string, unknown>;
  request_id: string | null;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown>;
  readonly requestId: string | null;

  constructor(status: number, body: ApiErrorBody) {
    super(body.message);
    this.name = 'ApiError';
    this.status = status;
    this.code = body.code;
    this.details = body.details ?? {};
    this.requestId = body.request_id ?? null;
  }
}

export type QueryValue = string | number | boolean | null | undefined;

export function buildQuery(params: Record<string, QueryValue>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue;
    search.set(key, String(value));
  }
  const query = search.toString();
  return query ? `?${query}` : '';
}

// --- session ------------------------------------------------------------------
//
// The browser's credential is an httpOnly cookie set by the server at login: page
// scripts cannot read it, so an XSS bug cannot steal the session. Because the
// browser attaches the cookie automatically, state-changing requests must also
// carry the session's CSRF token in `X-CSRF-Token`. That token is useless without
// the cookie, so it lives in memory only (never in storage) and is re-fetched from
// `GET /auth/me` after a page reload. Every request is still authorized again by
// the server - holding the cookie grants nothing the server-side role does not.

let csrfToken: string | null = null;

export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}

/** Fired when the server rejects the current session (expired, revoked, invalid). */
export const SESSION_ENDED_EVENT = 'ledgr:session-ended';

interface RequestOptions {
  method?: string;
  body?: unknown;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  /** Endpoints such as login/register: a 401 there is just "wrong password", not an ended session. */
  public?: boolean;
}

/** A response with what a screen sometimes needs besides the body. */
export interface DetailedResponse<T> {
  status: number;
  /** True when the server answered a retried request from its stored response (Idempotency-Key). */
  replayed: boolean;
  data: T;
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  return (await requestDetailed<T>(path, options)).data;
}

export async function requestDetailed<T>(
  path: string,
  options: RequestOptions = {},
): Promise<DetailedResponse<T>> {
  const { method = 'GET', body, headers = {}, signal, public: isPublic = false } = options;

  let response: Response;
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      method,
      signal,
      // Send and accept the httpOnly session cookie (CORS allows credentials for
      // the configured console origins only).
      credentials: 'include',
      headers: {
        Accept: 'application/json',
        ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
        ...(!isPublic && csrfToken && method !== 'GET' ? { 'X-CSRF-Token': csrfToken } : {}),
        ...headers,
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (cause) {
    throw new ApiError(0, {
      code: 'network_error',
      message: 'Cannot reach the LEDGR API.',
      details: { cause: String(cause) },
      request_id: null,
    });
  }

  if (response.status === 204) return { status: 204, replayed: false, data: undefined as T };

  const text = await response.text();
  // A gateway error page (HTML) or a truncated body must surface as an ApiError,
  // not as a raw SyntaxError the screens do not know how to show.
  let payload: unknown = null;
  let unparseable = false;
  if (text) {
    try {
      payload = JSON.parse(text) as unknown;
    } catch {
      unparseable = true;
    }
  }

  if (!response.ok || unparseable) {
    const errorBody = (payload as { error?: ApiErrorBody } | null)?.error;
    if (response.status === 401 && !isPublic) {
      // The server no longer accepts this session: tell the app to sign out.
      window.dispatchEvent(
        new CustomEvent(SESSION_ENDED_EVENT, { detail: { code: errorBody?.code ?? 'unauthorized' } }),
      );
    }
    throw new ApiError(
      response.status,
      errorBody ?? {
        code: 'unexpected_error',
        message: `Request failed with status ${response.status}`,
        details: {},
        request_id: response.headers.get('X-Request-ID'),
      },
    );
  }

  return {
    status: response.status,
    replayed: response.headers.get('Idempotent-Replay') === 'true',
    data: payload as T,
  };
}

/** Generates the idempotency key sent with a posting. */
export function newIdempotencyKey(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID();
  return `idem-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
}

/**
 * Downloads a server-generated file (CSV export, evidence report) with the
 * session cookie, then hands it to the browser as a normal file download.
 */
export async function downloadFile(path: string, fallbackName: string): Promise<void> {
  let response: Response;
  try {
    response = await fetch(`${BASE_URL}${path}`, { credentials: 'include' });
  } catch (cause) {
    throw new ApiError(0, {
      code: 'network_error',
      message: 'Cannot reach the LEDGR API.',
      details: { cause: String(cause) },
      request_id: null,
    });
  }
  if (!response.ok) {
    let body: { error?: ApiErrorBody } | null = null;
    try {
      body = (await response.json()) as { error?: ApiErrorBody };
    } catch {
      /* not JSON */
    }
    if (response.status === 401) {
      window.dispatchEvent(new CustomEvent(SESSION_ENDED_EVENT, { detail: { code: 'unauthorized' } }));
    }
    throw new ApiError(
      response.status,
      body?.error ?? {
        code: 'download_failed',
        message: `Download failed with status ${response.status}`,
        details: {},
        request_id: response.headers.get('X-Request-ID'),
      },
    );
  }
  const disposition = response.headers.get('Content-Disposition') ?? '';
  const name = /filename="?([^";]+)"?/.exec(disposition)?.[1] ?? fallbackName;
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}
