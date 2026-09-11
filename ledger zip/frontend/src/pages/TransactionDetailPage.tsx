/**
 * Transaction detail - the audit view.
 *
 * The layout answers one question: what exactly happened, and does it balance?
 * Debits and credits are placed side by side in the classic two-column form,
 * totalled independently, and the equality between them is asserted explicitly
 * rather than assumed.
 */

import { ArrowLeftRight, ChevronRight, RotateCcw, Undo2 } from 'lucide-react';
import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';

import { PageHeader } from '../components/layout/AppShell';
import {
  AccountRef,
  Amount,
  BalanceAssertion,
  CopyableId,
  DirectionBadge,
  TransactionStatusBadge,
} from '../components/ledger/atoms';
import { Modal } from '../components/ui/Modal';
import {
  Badge,
  Button,
  Field,
  Panel,
  PanelHeader,
  Skeleton,
  Spinner,
  Textarea,
} from '../components/ui/primitives';
import { ErrorState, Table, TableScroller, THead } from '../components/ui/Table';
import { ApiError } from '../lib/api';
import { cn } from '../lib/cn';
import { formatDateTime, formatInteger } from '../lib/format';
import { useAuditEvents, useReverseTransaction, useTransaction } from '../lib/queries';
import type { LedgerEntry, Transaction } from '../lib/types';

function DetailRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 px-4 py-2">
      <dt className="shrink-0 text-xs text-ink-3">{label}</dt>
      <dd className="min-w-0 text-right text-xs text-ink-2">{children}</dd>
    </div>
  );
}

function EntryColumn({
  title,
  entries,
  total,
  side,
}: {
  title: string;
  entries: LedgerEntry[];
  total: { amount_minor: number; currency: string; amount: string };
  side: 'debit' | 'credit';
}) {
  return (
    <div className="flex flex-col">
      <div
        className={cn(
          'flex items-center justify-between border-b px-4 py-2',
          side === 'debit'
            ? 'border-debit/25 bg-debit-soft/50'
            : 'border-credit/25 bg-credit-soft/50',
        )}
      >
        <span
          className={cn(
            'text-2xs font-semibold uppercase tracking-[0.08em]',
            side === 'debit' ? 'text-debit-ink' : 'text-credit-ink',
          )}
        >
          {title}
        </span>
        <span className="text-2xs text-ink-3">
          {entries.length} {entries.length === 1 ? 'entry' : 'entries'}
        </span>
      </div>

      <ul className="flex-1 divide-y divide-border">
        {entries.length === 0 ? (
          <li className="px-4 py-6 text-center text-xs text-ink-3">No entries on this side</li>
        ) : (
          entries.map((entry) => (
            <li key={entry.id} className="flex items-start justify-between gap-4 px-4 py-2.5">
              <div className="min-w-0">
                <AccountRef account={entry.account} />
                {entry.memo && <p className="mt-0.5 text-xs text-ink-3">{entry.memo}</p>}
              </div>
              <Amount money={entry.amount} tone={side} className="shrink-0 pt-0.5" />
            </li>
          ))
        )}
      </ul>

      <div className="flex items-center justify-between border-t border-border bg-surface-2 px-4 py-2">
        <span className="label">Total</span>
        <Amount money={total} showCurrency className="font-semibold" />
      </div>
    </div>
  );
}

function ReverseDialog({
  transaction,
  open,
  onClose,
}: {
  transaction: Transaction;
  open: boolean;
  onClose: () => void;
}) {
  const [reason, setReason] = useState('');
  const reverse = useReverseTransaction();
  const error = reverse.error instanceof ApiError ? reverse.error : null;

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Reverse transaction"
      description="Posts a mirrored compensating transaction. Nothing is edited or deleted."
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            disabled={reverse.isPending}
            icon={reverse.isPending ? <Spinner /> : <Undo2 className="h-3.5 w-3.5" />}
            onClick={() =>
              reverse.mutate(
                { id: transaction.id, reason: reason.trim() || undefined },
                { onSuccess: onClose },
              )
            }
          >
            Post reversal
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <div className="rounded-sm border border-border bg-surface-2 px-3 py-2.5 text-xs">
          <p className="num font-medium text-ink">{transaction.reference}</p>
          <p className="mt-0.5 text-ink-3">{transaction.description}</p>
          <p className="mt-1.5">
            <Amount money={transaction.total_debits} showCurrency className="font-semibold" /> across{' '}
            <span className="num">{transaction.entry_count}</span> entries
          </p>
        </div>

        <Field
          label="Reason"
          htmlFor="reason"
          hint="Recorded on the reversal and in the audit trail."
        >
          <Textarea
            id="reason"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="Deposit recalled by originating bank"
          />
        </Field>

        <p className="text-xs leading-relaxed text-ink-3">
          Every entry on this transaction is mirrored: each debit becomes a credit of the same
          amount and vice versa. The original stays exactly as posted and is marked{' '}
          <span className="font-medium text-ink-2">REVERSED</span>.
        </p>

        {error && (
          <p className="rounded-sm border border-negative/30 bg-negative-soft px-3 py-2 text-xs text-negative">
            <span className="num font-semibold">{error.code}</span> — {error.message}
          </p>
        )}
      </div>
    </Modal>
  );
}

export default function TransactionDetailPage() {
  const { transactionId } = useParams();
  const [reverseOpen, setReverseOpen] = useState(false);
  const query = useTransaction(transactionId);
  const audit = useAuditEvents({ resource_id: transactionId, limit: 20 }, Boolean(transactionId));

  if (query.isError) {
    return (
      <>
        <PageHeader title="Transaction" />
        <div className="px-6 py-5">
          <Panel>
            <ErrorState error={query.error} onRetry={() => query.refetch()} />
          </Panel>
        </div>
      </>
    );
  }

  const transaction = query.data;
  const debits = transaction?.entries.filter((entry) => entry.direction === 'DEBIT') ?? [];
  const credits = transaction?.entries.filter((entry) => entry.direction === 'CREDIT') ?? [];
  const canReverse =
    transaction?.status === 'POSTED' && transaction.kind === 'STANDARD';

  return (
    <>
      <PageHeader
        breadcrumb={
          <>
            <Link to="/transactions" className="hover:text-ink">
              Transactions
            </Link>
            <ChevronRight className="h-3 w-3" />
            <span className="num">{transaction?.reference ?? '…'}</span>
          </>
        }
        title={
          transaction ? (
            <span className="num">{transaction.reference}</span>
          ) : (
            'Loading transaction…'
          )
        }
        description={transaction?.description}
        actions={
          transaction && (
            <div className="flex items-center gap-2">
              <TransactionStatusBadge status={transaction.status} kind={transaction.kind} />
              {canReverse && (
                <Button
                  variant="danger"
                  icon={<RotateCcw className="h-3.5 w-3.5" strokeWidth={1.9} />}
                  onClick={() => setReverseOpen(true)}
                >
                  Reverse
                </Button>
              )}
            </div>
          )
        }
      />

      <div className="space-y-4 px-6 py-5">
        {!transaction ? (
          <>
            <Skeleton className="h-20 w-full" />
            <Skeleton className="h-64 w-full" />
          </>
        ) : (
          <>
            {(transaction.reverses_transaction_id || transaction.reversed_by_transaction_id) && (
              <div
                className={cn(
                  'flex flex-wrap items-center gap-2 rounded-md border px-4 py-2.5 text-xs',
                  'border-warning/35 bg-warning-soft',
                )}
              >
                <ArrowLeftRight className="h-3.5 w-3.5 text-warning" strokeWidth={1.9} />
                {transaction.reverses_transaction_id ? (
                  <span className="text-ink-2">
                    This is a reversal of{' '}
                    <Link
                      to={`/transactions/${transaction.reverses_transaction_id}`}
                      className="num font-medium text-accent hover:underline"
                    >
                      the original transaction
                    </Link>
                    . The original keeps every entry it was posted with.
                  </span>
                ) : (
                  <span className="text-ink-2">
                    Reversed on{' '}
                    <span className="num">{formatDateTime(transaction.reversed_at)}</span> by{' '}
                    <Link
                      to={`/transactions/${transaction.reversed_by_transaction_id}`}
                      className="num font-medium text-accent hover:underline"
                    >
                      a compensating transaction
                    </Link>
                    .
                  </span>
                )}
              </div>
            )}

            <BalanceAssertion
              debits={transaction.total_debits}
              credits={transaction.total_credits}
              balanced={transaction.balanced}
            />

            <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
              <Panel className="overflow-hidden xl:col-span-2">
                <PanelHeader
                  title="Entries"
                  description={`${formatInteger(transaction.entry_count)} entries, written in one atomic database transaction`}
                />
                <div className="grid grid-cols-1 divide-y divide-border md:grid-cols-2 md:divide-x md:divide-y-0">
                  <EntryColumn
                    title="Debits"
                    side="debit"
                    entries={debits}
                    total={transaction.total_debits}
                  />
                  <EntryColumn
                    title="Credits"
                    side="credit"
                    entries={credits}
                    total={transaction.total_credits}
                  />
                </div>
              </Panel>

              <Panel>
                <PanelHeader title="Transaction record" />
                <dl className="divide-y divide-border">
                  <DetailRow label="Status">
                    <TransactionStatusBadge status={transaction.status} kind={transaction.kind} />
                  </DetailRow>
                  <DetailRow label="Currency">
                    <span className="num">{transaction.currency}</span>
                  </DetailRow>
                  <DetailRow label="Sequence">
                    <span className="num">#{transaction.seq}</span>
                  </DetailRow>
                  <DetailRow label="Posted at (system)">
                    <span className="num">{formatDateTime(transaction.posted_at)}</span>
                  </DetailRow>
                  <DetailRow label="Effective at (business)">
                    <span className="num">{formatDateTime(transaction.effective_at)}</span>
                  </DetailRow>
                  <DetailRow label="Actor">
                    <span className="num">{transaction.actor}</span>
                  </DetailRow>
                  <DetailRow label="External reference">
                    {transaction.external_reference ? (
                      <span className="num">{transaction.external_reference}</span>
                    ) : (
                      <span className="text-ink-3">—</span>
                    )}
                  </DetailRow>
                  <DetailRow label="Idempotency key">
                    {transaction.idempotency_key ? (
                      <CopyableId value={transaction.idempotency_key} truncate={10} />
                    ) : (
                      <span className="text-ink-3">not supplied</span>
                    )}
                  </DetailRow>
                  <DetailRow label="Request ID">
                    {transaction.request_id ? (
                      <CopyableId value={transaction.request_id} truncate={10} />
                    ) : (
                      <span className="text-ink-3">—</span>
                    )}
                  </DetailRow>
                  <DetailRow label="Transaction ID">
                    <CopyableId value={transaction.id} truncate={12} />
                  </DetailRow>
                  {Object.keys(transaction.metadata).length > 0 && (
                    <DetailRow label="Metadata">
                      <div className="flex flex-wrap justify-end gap-1">
                        {Object.entries(transaction.metadata).map(([key, value]) => (
                          <Badge key={key} tone="neutral" mono>
                            {key}: {String(value)}
                          </Badge>
                        ))}
                      </div>
                    </DetailRow>
                  )}
                </dl>
              </Panel>
            </div>

            <Panel>
              <PanelHeader
                title="Ledger entries"
                description="Append-only rows exactly as stored; ordered by their position in the transaction"
              />
              <TableScroller>
                <Table>
                  <THead>
                    <th className="th">Seq</th>
                    <th className="th">Idx</th>
                    <th className="th">Account</th>
                    <th className="th">Type</th>
                    <th className="th">Dir</th>
                    <th className="th text-right">Debit</th>
                    <th className="th text-right">Credit</th>
                    <th className="th">Memo</th>
                    <th className="th">Entry ID</th>
                  </THead>
                  <tbody>
                    {transaction.entries.map((entry) => (
                      <tr key={entry.id}>
                        <td className="td num text-ink-3">{entry.seq}</td>
                        <td className="td num text-ink-3">{entry.entry_index}</td>
                        <td className="td">
                          <AccountRef account={entry.account} />
                        </td>
                        <td className="td num text-xs text-ink-3">{entry.account?.type}</td>
                        <td className="td">
                          <DirectionBadge direction={entry.direction} />
                        </td>
                        <td className="td text-right">
                          {entry.direction === 'DEBIT' ? (
                            <Amount money={entry.amount} tone="debit" />
                          ) : (
                            <span className="text-ink-3">—</span>
                          )}
                        </td>
                        <td className="td text-right">
                          {entry.direction === 'CREDIT' ? (
                            <Amount money={entry.amount} tone="credit" />
                          ) : (
                            <span className="text-ink-3">—</span>
                          )}
                        </td>
                        <td className="td max-w-[18rem] truncate text-ink-3">
                          {entry.memo ?? '—'}
                        </td>
                        <td className="td">
                          <CopyableId value={entry.id} truncate={8} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot>
                    <tr className="bg-surface-2">
                      <td className="td" colSpan={5}>
                        <span className="label">Totals</span>
                      </td>
                      <td className="td text-right">
                        <Amount money={transaction.total_debits} tone="debit" className="font-semibold" />
                      </td>
                      <td className="td text-right">
                        <Amount money={transaction.total_credits} tone="credit" className="font-semibold" />
                      </td>
                      <td className="td" colSpan={2} />
                    </tr>
                  </tfoot>
                </Table>
              </TableScroller>
            </Panel>

            <Panel>
              <PanelHeader
                title="Audit trail"
                description="Recorded in the same database transaction as the entries above"
              />
              {audit.data && audit.data.items.length > 0 ? (
                <ul className="divide-y divide-border">
                  {audit.data.items.map((event) => (
                    <li key={event.id} className="flex flex-wrap items-center gap-3 px-4 py-2.5">
                      <Badge tone="accent" mono>
                        {event.event_type}
                      </Badge>
                      <span className="num text-xs text-ink-2">{event.actor}</span>
                      <span className="num text-xs text-ink-3">
                        {formatDateTime(event.created_at)}
                      </span>
                      {event.request_id && (
                        <CopyableId value={event.request_id} label="request" truncate={10} />
                      )}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="px-4 py-4 text-xs text-ink-3">No audit events recorded.</p>
              )}
            </Panel>
          </>
        )}
      </div>

      {transaction && (
        <ReverseDialog
          transaction={transaction}
          open={reverseOpen}
          onClose={() => setReverseOpen(false)}
        />
      )}
    </>
  );
}
