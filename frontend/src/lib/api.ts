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

const ACTOR_STORAGE_KEY = 'ledgr.actor';
const API_KEY_STORAGE_KEY = 'ledgr.apiKey';

/**
 * A cosmetic display label only - as of the authentication hardening pass,
 * the backend no longer trusts (or even reads) this for the `actor` it
 * records. That identity now comes from the API key below, verified
 * server-side. This is kept only so the console can show "signed in as X"
 * without a second round trip; renaming it here changes nothing about who
 * a write is attributed to.
 */
export function getActor(): string {
  try {
    return localStorage.getItem(ACTOR_STORAGE_KEY)?.trim() || 'console';
  } catch {
    return 'console';
  }
}

export function setActor(name: string): void {
  try {
    const trimmed = name.trim();
    if (trimmed) localStorage.setItem(ACTOR_STORAGE_KEY, trimmed);
    else localStorage.removeItem(ACTOR_STORAGE_KEY);
  } catch {
    /* storage may be unavailable; display falls back to the default label */
  }
}

/**
 * The real credential. Sent as `Authorization: Bearer <key>` on every
 * request; the backend hashes it and looks it up against `api_keys` (see
 * `app.services.auth`), and the matching key's label - not anything set
 * above - becomes the `actor` recorded on whatever this request writes.
 * Reads work without one; every write endpoint rejects a request that
 * doesn't carry a valid key with 401.
 */
export function getApiKey(): string | null {
  try {
    return localStorage.getItem(API_KEY_STORAGE_KEY)?.trim() || null;
  } catch {
    return null;
  }
}

export function setApiKey(key: string): void {
  try {
    const trimmed = key.trim();
    if (trimmed) localStorage.setItem(API_KEY_STORAGE_KEY, trimmed);
    else localStorage.removeItem(API_KEY_STORAGE_KEY);
  } catch {
    /* storage may be unavailable; requests proceed unauthenticated and writes will 401 */
  }
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  headers?: Record<string, string>;
  signal?: AbortSignal;
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, headers = {}, signal } = options;

  let response: Response;
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      method,
      signal,
      headers: {
        Accept: 'application/json',
        ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
        // Server-verified identity for writes - see `getApiKey`. Reads don't
        // need one; a write sent without a valid key gets a 401 back.
        ...(getApiKey() ? { Authorization: `Bearer ${getApiKey()}` } : {}),
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

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  const payload = text ? (JSON.parse(text) as unknown) : null;

  if (!response.ok) {
    const errorBody = (payload as { error?: ApiErrorBody } | null)?.error;
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

  return payload as T;
}

/** Generates the idempotency key sent with a posting. */
export function newIdempotencyKey(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID();
  return `idem-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
}
