/**
 * Ledger-specific display atoms.
 *
 * The debit/credit colour pairing (muted slate blue / gold) is defined once
 * here and used everywhere, so an operator learns the mapping in one place
 * and it never shifts meaning. Green and red are reserved for system state
 * (balanced/unbalanced, healthy/unreachable) - never for direction, which is
 * neither good nor bad.
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
        'text-[0.8125rem] font-medium',
        direction === 'DEBIT' ? 'text-debit-ink' : 'text-credit-ink',
      )}
    >
      {direction === 'DEBIT' ? 'Debit' : 'Credit'}
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
    <span className="inline-flex items-center gap-1.5">
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
      <span className="num text-[0.875rem] font-medium text-ink group-hover:text-accent-ink">
        {account.code}
      </span>
      <span className="truncate text-[0.8125rem] text-ink-3">{account.name}</span>
    </>
  );

  if (!withLink) {
    return <span className={cn('flex min-w-0 flex-col leading-tight', className)}>{content}</span>;
  }
  return (
    <Link
      to={`/app/accounts/${account.id}`}
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
        'text-[0.8125rem] text-ink-2 transition-colors hover:border-border hover:bg-surface-2',
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
 * The core assertion of double-entry, stated plainly: total debits, total
 * credits, and a verdict - not a segmented equation block.
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
    <div className={cn('rounded-md border border-border bg-surface px-5 py-4', className)}>
      <div className="flex flex-wrap items-center gap-x-10 gap-y-3">
        <div>
          <p className="caption">Total debits</p>
          <p className="num mt-0.5 text-xl font-semibold text-ink">
            {formatMoney(debits, { registry: currencies, showCurrency: false })}
          </p>
        </div>
        <div>
          <p className="caption">Total credits</p>
          <p className="num mt-0.5 text-xl font-semibold text-ink">
            {formatMoney(credits, { registry: currencies, showCurrency: false })}
          </p>
        </div>
      </div>

      <div
        className={cn(
          'mt-4 flex items-center gap-2 border-t border-border pt-3 text-[0.875rem]',
          balanced ? 'text-positive' : 'text-negative',
        )}
      >
        {balanced ? (
          <Check className="h-4 w-4 shrink-0" strokeWidth={2.2} />
        ) : (
          <X className="h-4 w-4 shrink-0" strokeWidth={2.2} />
        )}
        <span className="font-medium">{balanced ? 'Balanced' : 'Out of balance'}</span>
        {!balanced && (
          <span className="num text-ink-2">
            · Difference:{' '}
            {formatMoney(
              { amount_minor: Math.abs(difference), currency: debits.currency, amount: '' },
              { registry: currencies, showCurrency: true },
            )}
          </span>
        )}
      </div>
    </div>
  );
}
