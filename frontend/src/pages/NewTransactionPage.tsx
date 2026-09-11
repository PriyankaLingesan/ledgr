/**
 * Transaction builder.
 *
 * The running debit/credit totals are the centre of this screen: the operator
 * must be able to see that a posting balances *before* submitting it. The
 * server enforces the same rule again - this is a convenience, not the control.
 */

import { AlertCircle, Check, Plus, RefreshCw, ShieldCheck, Trash2, X } from 'lucide-react';
import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { PageHeader } from '../components/layout/AppShell';
import { Amount, BalanceAssertion } from '../components/ledger/atoms';
import {
  Button,
  Field,
  IconButton,
  Input,
  Panel,
  PanelHeader,
  Select,
  Spinner,
} from '../components/ui/primitives';
import { ApiError, newIdempotencyKey } from '../lib/api';
import { cn } from '../lib/cn';
import { parseAmountToMinor } from '../lib/format';
import { useAccounts, useCreateTransaction, useCurrencies } from '../lib/queries';
import type { Account, EntryDirection } from '../lib/types';

interface DraftEntry {
  key: string;
  accountId: string;
  direction: EntryDirection;
  amount: string;
  memo: string;
}

function emptyEntry(direction: EntryDirection): DraftEntry {
  return {
    key: Math.random().toString(36).slice(2),
    accountId: '',
    direction,
    amount: '',
    memo: '',
  };
}

const ACCOUNT_TYPE_ORDER = ['ASSET', 'LIABILITY', 'EQUITY', 'REVENUE', 'EXPENSE'] as const;

export default function NewTransactionPage() {
  const navigate = useNavigate();
  const { data: currencies } = useCurrencies();
  const createTransaction = useCreateTransaction();

  const [currency, setCurrency] = useState('USD');
  const [description, setDescription] = useState('');
  const [reference, setReference] = useState('');
  const [externalReference, setExternalReference] = useState('');
  const [effectiveAt, setEffectiveAt] = useState('');
  const [idempotencyKey, setIdempotencyKey] = useState(() => newIdempotencyKey());
  const [entries, setEntries] = useState<DraftEntry[]>([
    emptyEntry('DEBIT'),
    emptyEntry('CREDIT'),
  ]);

  // Only ACTIVE accounts can receive entries, so only those are offered.
  const accountsQuery = useAccounts({ limit: 200, offset: 0, status: 'ACTIVE' });
  const accounts = accountsQuery.data?.items ?? [];

  const eligible = useMemo(
    () => accounts.filter((account) => account.currency === currency),
    [accounts, currency],
  );

  const grouped = useMemo(() => {
    const map = new Map<string, Account[]>();
    for (const account of eligible) {
      const list = map.get(account.type) ?? [];
      list.push(account);
      map.set(account.type, list);
    }
    return ACCOUNT_TYPE_ORDER.filter((type) => map.has(type)).map((type) => ({
      type,
      accounts: map.get(type)!,
    }));
  }, [eligible]);

  const parsed = entries.map((entry) => ({
    entry,
    minor: parseAmountToMinor(entry.amount, currency, currencies),
  }));

  const totalDebits = parsed
    .filter((row) => row.entry.direction === 'DEBIT')
    .reduce((sum, row) => sum + (row.minor ?? 0), 0);
  const totalCredits = parsed
    .filter((row) => row.entry.direction === 'CREDIT')
    .reduce((sum, row) => sum + (row.minor ?? 0), 0);

  const accountsChosen = entries.length >= 2 && parsed.every((row) => row.entry.accountId);
  const amountsValid =
    parsed.length > 0 && parsed.every((row) => row.minor !== null && row.minor > 0);
  const hasBothSides = totalDebits > 0 && totalCredits > 0;
  const balanced = hasBothSides && amountsValid && totalDebits === totalCredits;

  const checks = [
    { label: 'Description provided', ok: description.trim().length > 0 },
    { label: 'Accounts selected', ok: accountsChosen },
    { label: 'Amounts valid', ok: amountsValid },
    { label: 'Debits equal credits', ok: balanced },
  ];
  const canSubmit = checks.every((c) => c.ok) && !createTransaction.isPending;
  const apiError = createTransaction.error instanceof ApiError ? createTransaction.error : null;

  const update = (key: string, patch: Partial<DraftEntry>) =>
    setEntries((current) =>
      current.map((entry) => (entry.key === key ? { ...entry, ...patch } : entry)),
    );

  const submit = () => {
    // Belt-and-braces against a double-click outrunning the `disabled` prop's
    // next render: the same idempotency key would make a genuine race safe
    // anyway, but there is no reason to make the server arbitrate this one.
    if (createTransaction.isPending) return;
    createTransaction.mutate(
      {
        description: description.trim(),
        currency,
        reference: reference.trim() || null,
        external_reference: externalReference.trim() || null,
        effective_at: effectiveAt ? new Date(effectiveAt).toISOString() : null,
        entries: parsed.map((row) => ({
          account_id: row.entry.accountId,
          direction: row.entry.direction,
          amount_minor: row.minor ?? 0,
          memo: row.entry.memo.trim() || null,
        })),
        idempotencyKey,
      },
      {
        onSuccess: (transaction) => navigate(`/app/transactions/${transaction.id}`),
      },
    );
  };

  return (
    <>
      <PageHeader
        title="Post a transaction"
        description="Entries are written atomically: either all of them land, or none do."
        actions={
          <div className="flex items-center gap-2">
            <Button onClick={() => navigate(-1)}>Cancel</Button>
            <Button
              variant="primary"
              disabled={!canSubmit}
              onClick={submit}
              icon={createTransaction.isPending ? <Spinner /> : undefined}
            >
              Post transaction
            </Button>
          </div>
        }
      />

      <div className="space-y-4 px-5 py-6 sm:px-8">
        <Panel>
          <PanelHeader title="Transaction" description="Applies to every entry below" />
          <div className="grid grid-cols-1 gap-3 px-4 py-4 md:grid-cols-4">
            <Field label="Description" required htmlFor="description" className="md:col-span-2">
              <Input
                id="description"
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                placeholder="Processor settlement for 08 Sep"
              />
            </Field>
            <Field
              label="Currency"
              htmlFor="currency"
              hint="Every account involved must use this currency"
            >
              <Select
                id="currency"
                value={currency}
                onChange={(event) => {
                  setCurrency(event.target.value);
                  // Accounts are currency-bound; a change invalidates the picks.
                  setEntries((current) => current.map((entry) => ({ ...entry, accountId: '' })));
                }}
              >
                {(currencies ?? []).map((option) => (
                  <option key={option.code} value={option.code}>
                    {option.code} — {option.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field
              label="Effective date"
              htmlFor="effective"
              hint="Business time. Defaults to now."
            >
              <Input
                id="effective"
                type="datetime-local"
                value={effectiveAt}
                onChange={(event) => setEffectiveAt(event.target.value)}
              />
            </Field>
            <Field
              label="Reference"
              htmlFor="reference"
              hint="Optional. Must be unique; blocks duplicates."
              className="md:col-span-2"
            >
              <Input
                id="reference"
                value={reference}
                onChange={(event) => setReference(event.target.value)}
                placeholder="Generated automatically if left blank"
                className="num"
              />
            </Field>
            <Field
              label="External reference"
              htmlFor="external"
              hint="Upstream system identifier"
              className="md:col-span-2"
            >
              <Input
                id="external"
                value={externalReference}
                onChange={(event) => setExternalReference(event.target.value)}
                placeholder="stl_8891042771"
                className="num"
              />
            </Field>
          </div>
        </Panel>

        <Panel>
          <PanelHeader
            title="Entries"
            description="Add the debit and credit lines for this transaction."
            actions={
              <div className="flex items-center gap-1.5">
                <Button
                  size="sm"
                  icon={<Plus className="h-3.5 w-3.5" />}
                  onClick={() => setEntries((current) => [...current, emptyEntry('DEBIT')])}
                >
                  Debit line
                </Button>
                <Button
                  size="sm"
                  icon={<Plus className="h-3.5 w-3.5" />}
                  onClick={() => setEntries((current) => [...current, emptyEntry('CREDIT')])}
                >
                  Credit line
                </Button>
              </div>
            }
          />

          {eligible.length === 0 && !accountsQuery.isLoading && (
            <p className="flex items-center gap-2 border-b border-warning/30 bg-warning-soft px-4 py-2.5 text-[0.8125rem] text-ink-2">
              <AlertCircle className="h-4 w-4 text-warning" strokeWidth={1.9} />
              No active {currency} accounts exist yet. Create accounts in that currency first.
            </p>
          )}

          <div className="divide-y divide-border">
            {entries.map((entry, index) => {
              const minor = parsed[index].minor;
              const invalidAmount = entry.amount.trim() !== '' && (minor === null || minor <= 0);

              return (
                <div
                  key={entry.key}
                  className={cn(
                    'grid grid-cols-12 items-start gap-3 px-4 py-3',
                    entry.direction === 'DEBIT'
                      ? 'border-l-2 border-l-debit'
                      : 'border-l-2 border-l-credit',
                  )}
                >
                  <div className="col-span-12 md:col-span-2">
                    <div className="flex items-center gap-px rounded-sm border border-border p-0.5">
                      {(['DEBIT', 'CREDIT'] as const).map((direction) => (
                        <button
                          key={direction}
                          type="button"
                          onClick={() => update(entry.key, { direction })}
                          className={cn(
                            'flex-1 rounded-xs px-2 py-1.5 text-[0.8125rem] font-medium transition-colors',
                            entry.direction === direction
                              ? direction === 'DEBIT'
                                ? 'bg-debit-soft text-debit-ink'
                                : 'bg-credit-soft text-credit-ink'
                              : 'text-ink-3 hover:text-ink',
                          )}
                        >
                          {direction === 'DEBIT' ? 'Debit' : 'Credit'}
                        </button>
                      ))}
                    </div>
                  </div>

                  <div className="col-span-12 md:col-span-4">
                    <Select
                      value={entry.accountId}
                      onChange={(event) => update(entry.key, { accountId: event.target.value })}
                      className="num"
                    >
                      <option value="">Select an account…</option>
                      {grouped.map((group) => (
                        <optgroup key={group.type} label={group.type}>
                          {group.accounts.map((account) => (
                            <option key={account.id} value={account.id}>
                              {account.code} — {account.name}
                            </option>
                          ))}
                        </optgroup>
                      ))}
                    </Select>
                  </div>

                  <div className="col-span-6 md:col-span-2">
                    <Input
                      value={entry.amount}
                      onChange={(event) => update(entry.key, { amount: event.target.value })}
                      placeholder="0.00"
                      inputMode="decimal"
                      className={cn('num text-right', invalidAmount && 'border-negative')}
                    />
                  </div>

                  <div className="col-span-5 md:col-span-3">
                    <Input
                      value={entry.memo}
                      onChange={(event) => update(entry.key, { memo: event.target.value })}
                      placeholder="Memo (optional)"
                    />
                  </div>

                  <div className="col-span-1 flex justify-end pt-0.5">
                    <IconButton
                      title="Remove entry"
                      disabled={entries.length <= 2}
                      onClick={() =>
                        setEntries((current) => current.filter((row) => row.key !== entry.key))
                      }
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </IconButton>
                  </div>
                </div>
              );
            })}
          </div>
        </Panel>

        <BalanceAssertion
          debits={{ amount_minor: totalDebits, currency, amount: '' }}
          credits={{ amount_minor: totalCredits, currency, amount: '' }}
          balanced={balanced}
        />

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          <Panel className="lg:col-span-2">
            <PanelHeader
              title={canSubmit ? 'Ready to post' : 'Before you post'}
              description="The same rules the API enforces."
            />
            <ul className="divide-y divide-border">
              {checks.map((check) => (
                <li
                  key={check.label}
                  className={cn(
                    'flex items-center gap-2.5 px-4 py-2.5 text-[0.875rem]',
                    check.ok ? 'text-ink-2' : 'text-ink-3',
                  )}
                >
                  {check.ok ? (
                    <Check className="h-4 w-4 shrink-0 text-positive" strokeWidth={2.2} />
                  ) : (
                    <span className="ml-0.5 mr-0.5 h-2.5 w-2.5 shrink-0 rounded-full border-2 border-border-strong" />
                  )}
                  {check.label}
                </li>
              ))}
            </ul>
            {apiError && (
              <div className="border-t border-border px-4 py-3">
                <p className="flex items-start gap-2 text-[0.8125rem] text-negative">
                  <X className="mt-0.5 h-4 w-4 shrink-0" strokeWidth={2.2} />
                  <span>{apiError.message}</span>
                </p>
                {Object.keys(apiError.details).length > 0 && (
                  <pre className="num mt-2 max-h-32 overflow-auto rounded-sm border border-border bg-surface-2 p-2 text-2xs text-ink-2">
                    {JSON.stringify(apiError.details, null, 2)}
                  </pre>
                )}
              </div>
            )}
          </Panel>

          <Panel>
            <PanelHeader title="Request safety" />
            <div className="space-y-3 px-4 py-4">
              <p className="flex items-start gap-2 text-[0.8125rem] leading-relaxed text-ink-2">
                <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-accent-ink" strokeWidth={1.7} />
                This transaction uses an idempotency key to prevent duplicate posting if a request
                is retried.
              </p>
              <div className="flex items-center gap-2">
                <code className="num flex-1 truncate rounded-sm border border-border bg-surface-2 px-2 py-1.5 text-[0.75rem] text-ink-3">
                  {idempotencyKey}
                </code>
                <IconButton
                  title="Generate a new key"
                  onClick={() => setIdempotencyKey(newIdempotencyKey())}
                >
                  <RefreshCw className="h-3.5 w-3.5" />
                </IconButton>
              </div>
              <div className="flex items-center justify-between border-t border-border pt-3 text-[0.8125rem]">
                <span className="text-ink-3">Net movement</span>
                <Amount money={{ amount_minor: totalDebits, currency, amount: '' }} showCurrency />
              </div>
            </div>
          </Panel>
        </div>
      </div>
    </>
  );
}
