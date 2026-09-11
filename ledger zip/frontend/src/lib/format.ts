/**
 * Presentation helpers.
 *
 * Amounts arrive as integer minor units plus a currency; the exponent needed to
 * render them comes from the API's own currency registry, so the client never
 * hard-codes a second opinion about how many decimals a currency has.
 */

import type { Currency, Money } from './types';

const DEFAULT_EXPONENTS: Record<string, number> = { JPY: 0, KRW: 0, KWD: 3, BHD: 3 };

export function exponentFor(currency: string, registry?: Currency[]): number {
  const found = registry?.find((c) => c.code === currency);
  if (found) return found.exponent;
  return DEFAULT_EXPONENTS[currency] ?? 2;
}

/** Full monetary rendering, e.g. "1,250.50 USD". */
export function formatMoney(
  money: Money | null | undefined,
  options: { registry?: Currency[]; showCurrency?: boolean; signed?: boolean } = {},
): string {
  if (!money) return '—';
  const { registry, showCurrency = true, signed = false } = options;
  const exponent = exponentFor(money.currency, registry);
  const value = money.amount_minor / 10 ** exponent;
  const formatted = new Intl.NumberFormat('en-US', {
    minimumFractionDigits: exponent,
    maximumFractionDigits: exponent,
    signDisplay: signed ? 'exceptZero' : 'auto',
  }).format(value);
  return showCurrency ? `${formatted} ${money.currency}` : formatted;
}

export function formatMinor(
  amountMinor: number,
  currency: string,
  registry?: Currency[],
  showCurrency = false,
): string {
  return formatMoney({ amount_minor: amountMinor, currency, amount: '' }, {
    registry,
    showCurrency,
  });
}

/** Parses operator input in major units into integer minor units. */
export function parseAmountToMinor(
  input: string,
  currency: string,
  registry?: Currency[],
): number | null {
  const trimmed = input.trim().replace(/,/g, '');
  if (!trimmed) return null;
  if (!/^\d*(\.\d*)?$/.test(trimmed)) return null;

  const exponent = exponentFor(currency, registry);
  const [whole, fraction = ''] = trimmed.split('.');
  if (fraction.length > exponent) return null;
  const padded = fraction.padEnd(exponent, '0');
  const combined = `${whole || '0'}${padded}`;
  const value = Number(combined);
  return Number.isSafeInteger(value) ? value : null;
}

export function formatCompactNumber(value: number): string {
  return new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 }).format(
    value,
  );
}

export function formatInteger(value: number): string {
  return new Intl.NumberFormat('en-US').format(value);
}

const DATE_TIME = new Intl.DateTimeFormat('en-GB', {
  year: 'numeric',
  month: 'short',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
});

const DATE_ONLY = new Intl.DateTimeFormat('en-GB', {
  year: 'numeric',
  month: 'short',
  day: '2-digit',
});

const TIME_ONLY = new Intl.DateTimeFormat('en-GB', {
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
});

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  return DATE_TIME.format(new Date(iso));
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  return DATE_ONLY.format(new Date(iso));
}

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  return TIME_ONLY.format(new Date(iso));
}

export function formatRelative(iso: string | null | undefined): string {
  if (!iso) return '—';
  const seconds = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 45) return 'just now';
  if (seconds < 5400) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 86400 * 2) return `${Math.round(seconds / 3600)}h ago`;
  return `${Math.round(seconds / 86400)}d ago`;
}

/** Shortens a UUID for dense tables while keeping it recognisable. */
export function shortId(id: string): string {
  return id.length > 12 ? `${id.slice(0, 8)}…` : id;
}

export function titleCase(value: string): string {
  return value.charAt(0) + value.slice(1).toLowerCase();
}
