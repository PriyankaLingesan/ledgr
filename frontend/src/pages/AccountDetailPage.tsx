/**
 * Account detail.
 *
 * The headline balance here is re-derived from ledger entries on every read;
 * the cached figure is displayed next to it so any divergence is visible rather
 * than quietly served.
 */

import { ChevronRight, ShieldAlert, ShieldCheck } from 'lucide-react';
import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';

import { PageHeader } from '../components/layout/AppShell';
import {
  AccountStatusBadge,
  AccountTypeBadge,
  Amount,
  CopyableId,
  DirectionBadge,
  TransactionStatusBadge,
} from '../components/ledger/atoms';
import { Panel, PanelHeader, Skeleton } from '../components/ui/primitives';
import {
  EmptyState,
  ErrorState,
  Pagination,
  Table,
  TableScroller,
  TableSkeleton,
  THead,
} from '../components/ui/Table';
import { cn } from '../lib/cn';
import { formatDateTime, formatInteger } from '../lib/format';
import {
  useAccount,
  useAccountBalance,
  useAccountEntries,
  useAccountTransactions,
} from '../lib/queries';

const LIMIT = 25;

function Metric({
  label,
  children,
  hint,
}: {
  label: string;
  children: React.ReactNode;
  hint?: React.ReactNode;
}) {
  return (
    <div className="px-4 py-3.5">
      <p className="caption">{label}</p>
      <div className="mt-1 text-lg font-semibold text-ink">{children}</div>
      {hint && <p className="mt-0.5 text-[0.8125rem] text-ink-3">{hint}</p>}
    </div>
  );
}

export default function AccountDetailPage() {
  const { accountId } = useParams();
  const navigate = useNavigate();
  const [tab, setTab] = useState<'entries' | 'transactions'>('entries');
  const [offset, setOffset] = useState(0);

  const account = useAccount(accountId);
  const balance = useAccountBalance(accountId);
  const entries = useAccountEntries(accountId, { limit: LIMIT, offset });
  const transactions = useAccountTransactions(accountId, {
    limit: LIMIT,
    offset: tab === 'transactions' ? offset : 0,
  });

  if (account.isError) {
    return (
      <>
        <PageHeader title="Account" />
        <div className="px-5 py-6 sm:px-8">
          <Panel>
            <ErrorState error={account.error} onRetry={() => account.refetch()} />
          </Panel>
        </div>
      </>
    );
  }

  const data = account.data;
  const normalIsDebit = data?.normal_balance === 'DEBIT';

  return (
    <>
      <PageHeader
        breadcrumb={
          <>
            <Link to="/app/accounts" className="hover:text-ink">
              Accounts
            </Link>
            <ChevronRight className="h-3 w-3" />
            <span className="num">{data?.code ?? '…'}</span>
          </>
        }
        title={
          data ? (
            <span className="flex items-center gap-3">
              <span className="num">{data.code}</span>
              <span className="text-sm font-normal text-ink-3">{data.name}</span>
            </span>
          ) : (
            'Loading account…'
          )
        }
        actions={
          data && (
            <div className="flex items-center gap-2">
              <AccountTypeBadge type={data.type} />
              <AccountStatusBadge status={data.status} />
            </div>
          )
        }
      />

      <div className="space-y-4 px-5 py-6 sm:px-8">
        <Panel>
          <div className="grid grid-cols-2 divide-x divide-border md:grid-cols-4">
            <Metric
              label={`Derived balance (${normalIsDebit ? 'debit' : 'credit'} normal)`}
              hint="Aggregated from ledger entries at read time"
            >
              {balance.data ? (
                <Amount money={balance.data.balance} showCurrency tone="auto" className="text-xl" />
              ) : (
                <Skeleton className="h-6 w-32" />
              )}
            </Metric>
            <Metric label="Total debits" hint={`${balance.data?.entry_count ?? 0} entries in total`}>
              {balance.data ? (
                <Amount money={balance.data.debits} tone="debit" className="text-xl" />
              ) : (
                <Skeleton className="h-6 w-24" />
              )}
            </Metric>
            <Metric label="Total credits" hint={`Last entry #${balance.data?.last_entry_seq ?? '—'}`}>
              {balance.data ? (
                <Amount money={balance.data.credits} tone="credit" className="text-xl" />
              ) : (
                <Skeleton className="h-6 w-24" />
              )}
            </Metric>
            <Metric
              label="Cache agreement"
              hint={
                balance.data?.cache_consistent
                  ? 'Cached balance equals the derived balance'
                  : 'Cached balance diverges from ledger entries'
              }
            >
              {balance.data ? (
                <span
                  className={cn(
                    'inline-flex items-center gap-1.5 text-sm',
                    balance.data.cache_consistent ? 'text-positive' : 'text-negative',
                  )}
                >
                  {balance.data.cache_consistent ? (
                    <ShieldCheck className="h-4 w-4" strokeWidth={1.9} />
                  ) : (
                    <ShieldAlert className="h-4 w-4" strokeWidth={1.9} />
                  )}
                  {balance.data.cache_consistent ? 'Consistent' : 'Divergent'}
                </span>
              ) : (
                <Skeleton className="h-6 w-24" />
              )}
            </Metric>
          </div>

          <dl className="grid grid-cols-2 gap-x-6 gap-y-2 border-t border-border px-4 py-3 text-xs md:grid-cols-4">
            <div className="flex justify-between gap-2">
              <dt className="text-ink-3">Currency</dt>
              <dd className="num text-ink-2">{data?.currency ?? '—'}</dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt className="text-ink-3">Normal balance</dt>
              <dd className="num text-ink-2">{data?.normal_balance ?? '—'}</dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt className="text-ink-3">Balance floor</dt>
              <dd className="text-ink-2">
                {data ? (data.allows_negative_balance ? 'not enforced' : 'enforced at 0') : '—'}
              </dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt className="text-ink-3">Opened</dt>
              <dd className="num text-ink-2">{formatDateTime(data?.created_at)}</dd>
            </div>
            <div className="col-span-2 flex justify-between gap-2 md:col-span-4">
              <dt className="text-ink-3">Account ID</dt>
              <dd>{data && <CopyableId value={data.id} truncate={0} />}</dd>
            </div>
          </dl>
        </Panel>

        <Panel>
          <PanelHeader
            title={
              <div className="flex items-center gap-1">
                {(['entries', 'transactions'] as const).map((value) => (
                  <button
                    key={value}
                    type="button"
                    onClick={() => {
                      setTab(value);
                      setOffset(0);
                    }}
                    className={cn(
                      'rounded-sm px-2 py-1 text-xs font-medium capitalize transition-colors',
                      tab === value
                        ? 'bg-accent-soft text-accent-ink'
                        : 'text-ink-3 hover:bg-surface-3 hover:text-ink',
                    )}
                  >
                    {value === 'entries' ? 'Ledger entries' : 'Transactions'}
                  </button>
                ))}
              </div>
            }
            description={
              tab === 'entries'
                ? 'Every entry that shaped this balance, newest first'
                : 'Transactions with at least one entry against this account'
            }
          />

          {tab === 'entries' ? (
            entries.isLoading ? (
              <TableSkeleton rows={8} columns={6} />
            ) : entries.data && entries.data.items.length === 0 ? (
              <EmptyState
                title="No entries yet"
                description="This account has never been posted to."
              />
            ) : (
              <>
                <TableScroller>
                  <Table>
                    <THead>
                      <th className="th">#</th>
                      <th className="th">Posted</th>
                      <th className="th">Transaction</th>
                      <th className="th">Dir</th>
                      <th className="th text-right">Debit</th>
                      <th className="th text-right">Credit</th>
                      <th className="th text-right">Effect</th>
                      <th className="th">Memo</th>
                    </THead>
                    <tbody>
                      {entries.data?.items.map((entry) => {
                        const isDebit = entry.direction === 'DEBIT';
                        const effect =
                          (normalIsDebit ? 1 : -1) * (isDebit ? 1 : -1) * entry.amount.amount_minor;
                        return (
                          <tr
                            key={entry.id}
                            className="row-link"
                            onClick={() => navigate(`/app/transactions/${entry.transaction_id}`)}
                          >
                            <td className="td num text-ink-3">{entry.seq}</td>
                            <td className="td whitespace-nowrap text-ink-3">
                              {formatDateTime(entry.created_at)}
                            </td>
                            <td className="td">
                              <span className="num text-ink">
                                {entry.transaction?.reference ?? '—'}
                              </span>
                              <span className="block max-w-[18rem] truncate text-xs text-ink-3">
                                {entry.transaction?.description}
                              </span>
                            </td>
                            <td className="td">
                              <DirectionBadge direction={entry.direction} />
                            </td>
                            <td className="td text-right">
                              {isDebit ? <Amount money={entry.amount} tone="debit" /> : <span className="text-ink-3">—</span>}
                            </td>
                            <td className="td text-right">
                              {!isDebit ? <Amount money={entry.amount} tone="credit" /> : <span className="text-ink-3">—</span>}
                            </td>
                            <td className="td text-right">
                              <Amount
                                money={{
                                  amount_minor: effect,
                                  currency: entry.amount.currency,
                                  amount: '',
                                }}
                                signed
                                tone="auto"
                              />
                            </td>
                            <td className="td max-w-[16rem] truncate text-ink-3">
                              {entry.memo ?? '—'}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </Table>
                </TableScroller>
                <Pagination
                  total={entries.data?.total ?? 0}
                  limit={LIMIT}
                  offset={offset}
                  onChange={setOffset}
                  unit="entries"
                />
              </>
            )
          ) : transactions.isLoading ? (
            <TableSkeleton rows={8} columns={5} />
          ) : transactions.data && transactions.data.items.length === 0 ? (
            <EmptyState title="No transactions reference this account" />
          ) : (
            <>
              <TableScroller>
                <Table>
                  <THead>
                    <th className="th">Reference</th>
                    <th className="th">Description</th>
                    <th className="th">Posted</th>
                    <th className="th text-right">Amount</th>
                    <th className="th text-right">Legs</th>
                    <th className="th">Status</th>
                  </THead>
                  <tbody>
                    {transactions.data?.items.map((transaction) => (
                      <tr
                        key={transaction.id}
                        className="row-link"
                        onClick={() => navigate(`/app/transactions/${transaction.id}`)}
                      >
                        <td className="td num font-medium">{transaction.reference}</td>
                        <td className="td max-w-[22rem] truncate text-ink-2">
                          {transaction.description}
                        </td>
                        <td className="td whitespace-nowrap text-ink-3">
                          {formatDateTime(transaction.posted_at)}
                        </td>
                        <td className="td text-right">
                          <Amount money={transaction.total_debits} showCurrency />
                        </td>
                        <td className="td num text-right text-ink-3">
                          {formatInteger(transaction.entry_count)}
                        </td>
                        <td className="td">
                          <TransactionStatusBadge
                            status={transaction.status}
                            kind={transaction.kind}
                          />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              </TableScroller>
              <Pagination
                total={transactions.data?.total ?? 0}
                limit={LIMIT}
                offset={offset}
                onChange={setOffset}
                unit="transactions"
              />
            </>
          )}
        </Panel>
      </div>
    </>
  );
}
