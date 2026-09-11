/**
 * Ledger-specific display atoms.
 *
 * The debit/credit colour pairing (blue / amber) is defined once here and used
 * by every screen, so an operator learns the mapping in one place and it never
 * shifts meaning. Green and red are never used for direction - they would imply
 * good/bad, and a debit is neither.
 */

import { Check, Copy, X } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import { cn } from '../../lib/cn';
import { formatMoney } from '../../lib/format';
import { useCurrencies } from '../../lib/queries';
import type {
  AccountStatus,
  AccountSummary,
  AccountType,
  EntryDirection,
  Money,
  TransactionKind,
  TransactionStatus,
} from '../../lib/types';
import { Badge } from '../ui/primitives';

export function DirectionBadge({ direction }: { direction: EntryDirection }) {
  return (
    <Badge tone={direction === 'DEBIT' ? 'debit' : 'credit'}>
      {direction === 'DEBIT' ? 'DR' : 'CR'}
    </Badge>
  );
}

export function DirectionLabel({ direction }: { direction: EntryDirection }) {
  return (
    <span
      className={cn(
        'text-2xs font-semibold uppercase tracking-[0.06em]',
        direction === 'DEBIT' ? 'text-debit-ink' : 'text-credit-ink',
      )}
    >
      {direction}
    </span>
  );
}

/** Right-aligned monetary figure with tabular digits. */
export function Amount({
  money,
  showCurrency = false,
  signed = false,
  tone,
  className,
}: {
  money: Money | null | undefined;
  showCurrency?: boolean;
  signed?: boolean;
  tone?: 'debit' | 'credit' | 'auto' | 'muted';
  className?: string;
}) {
  const { data: currencies } = useCurrencies();
  if (!money) return <span className="num text-ink-3">—</span>;

  const negative = money.amount_minor < 0;
  const toneClass =
    tone === 'debit'
      ? 'text-debit-ink'
      : tone === 'credit'
        ? 'text-credit-ink'
        : tone === 'muted'
          ? 'text-ink-3'
          : tone === 'auto' && negative
            ? 'text-negative'
            : 'text-ink';

  return (
    <span className={cn('num tabular-nums', toneClass, className)}>
      {formatMoney(money, { registry: currencies, showCurrency, signed })}
    </span>
  );
}

const ACCOUNT_TYPE_TONE: Record<AccountType, 'neutral' | 'accent' | 'warning'> = {
  ASSET: 'accent',
  LIABILITY: 'warning',
  EQUITY: 'neutral',
  REVENUE: 'neutral',
  EXPENSE: 'neutral',
};

export function AccountTypeBadge({ type }: { type: AccountType }) {
  return <Badge tone={ACCOUNT_TYPE_TONE[type]}>{type}</Badge>;
}

export function AccountStatusBadge({ status }: { status: AccountStatus }) {
  const tone = status === 'ACTIVE' ? 'positive' : status === 'FROZEN' ? 'warning' : 'neutral';
  return <Badge tone={tone}>{status}</Badge>;
}

export function TransactionStatusBadge({
  status,
  kind,
}: {
  status: TransactionStatus;
  kind?: TransactionKind;
}) {
  return (
    <span className="inline-flex items-center gap-1">
      <Badge tone={status === 'POSTED' ? 'positive' : 'warning'}>{status}</Badge>
      {kind === 'REVERSAL' && <Badge tone="neutral">REVERSAL</Badge>}
    </span>
  );
}

/** Account reference used inside tables: code is the identifier, name the gloss. */
export function AccountRef({
  account,
  className,
  withLink = true,
}: {
  account: AccountSummary | null | undefined;
  className?: string;
  withLink?: boolean;
}) {
  if (!account) return <span className="text-ink-3">—</span>;

  const content = (
    <>
      <span className="num text-[0.8125rem] font-medium text-ink group-hover:text-accent">
        {account.code}
      </span>
      <span className="truncate text-xs text-ink-3">{account.name}</span>
    </>
  );

  if (!withLink) {
    return <span className={cn('flex min-w-0 flex-col leading-tight', className)}>{content}</span>;
  }
  return (
    <Link
      to={`/accounts/${account.id}`}
      className={cn('group flex min-w-0 flex-col leading-tight', className)}
    >
      {content}
    </Link>
  );
}

export function CopyableId({
  value,
  label,
  truncate = 8,
  className,
}: {
  value: string;
  label?: string;
  truncate?: number;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);

  return (
    <button
      type="button"
      title={value}
      onClick={() => {
        void navigator.clipboard?.writeText(value);
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1200);
      }}
      className={cn(
        'group inline-flex items-center gap-1.5 rounded-xs border border-transparent px-1 py-px',
        'text-xs text-ink-2 transition-colors hover:border-border hover:bg-surface-2',
        className,
      )}
    >
      {label && <span className="text-ink-3">{label}</span>}
      <span className="num">{truncate ? `${value.slice(0, truncate)}…` : value}</span>
      {copied ? (
        <Check className="h-3 w-3 text-positive" />
      ) : (
        <Copy className="h-3 w-3 text-ink-3 opacity-0 transition-opacity group-hover:opacity-100" />
      )}
    </button>
  );
}

/**
 * The core assertion of double-entry, rendered as an equation the operator can
 * check at a glance: total debits, an equals sign, total credits.
 */
export function BalanceAssertion({
  debits,
  credits,
  balanced,
  className,
}: {
  debits: Money;
  credits: Money;
  balanced: boolean;
  className?: string;
}) {
  const { data: currencies } = useCurrencies();
  const difference = debits.amount_minor - credits.amount_minor;

  return (
    <div
      className={cn(
        'flex flex-wrap items-stretch gap-px overflow-hidden rounded-md border',
        balanced ? 'border-border' : 'border-negative/40',
        className,
      )}
    >
      <div className="flex-1 bg-surface px-4 py-3">
        <p className="label text-debit-ink">Total debits</p>
        <p className="num mt-1 text-lg font-semibold text-ink">
          {formatMoney(debits, { registry: currencies, showCurrency: false })}
        </p>
      </div>

      <div
        className={cn(
          'flex w-12 shrink-0 items-center justify-center text-lg font-semibold',
          balanced ? 'bg-surface-2 text-ink-3' : 'bg-negative-soft text-negative',
        )}
        aria-hidden
      >
        {balanced ? '=' : '≠'}
      </div>

      <div className="flex-1 bg-surface px-4 py-3">
        <p className="label text-credit-ink">Total credits</p>
        <p className="num mt-1 text-lg font-semibold text-ink">
          {formatMoney(credits, { registry: currencies, showCurrency: false })}
        </p>
      </div>

      <div
        className={cn(
          'flex min-w-44 flex-col justify-center px-4 py-3',
          balanced ? 'bg-positive-soft' : 'bg-negative-soft',
        )}
      >
        <p className={cn('label', balanced ? 'text-positive' : 'text-negative')}>
          {balanced ? 'Balanced' : 'Out of balance'}
        </p>
        <p className="mt-1 flex items-center gap-1.5 text-xs">
          {balanced ? (
            <Check className="h-3.5 w-3.5 text-positive" strokeWidth={2.4} />
          ) : (
            <X className="h-3.5 w-3.5 text-negative" strokeWidth={2.4} />
          )}
          <span className={cn('num', balanced ? 'text-positive' : 'text-negative')}>
            Δ {formatMoney(
              { amount_minor: difference, currency: debits.currency, amount: '' },
              { registry: currencies, showCurrency: true },
            )}
          </span>
        </p>
      </div>
    </div>
  );
}
