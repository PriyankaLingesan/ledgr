/**
 * Ledger overview.
 *
 * Every number on this page is derived from ledger entries by the API. There
 * are no invented metrics, no growth percentages against imaginary baselines
 * and no vanity counters.
 */

import { ArrowUpRight, CheckCircle2, ShieldAlert, ShieldCheck } from 'lucide-react';
import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { PageHeader } from '../components/layout/AppShell';
import {
  AccountRef,
  Amount,
  DirectionBadge,
  TransactionStatusBadge,
} from '../components/ledger/atoms';
import { Badge, Button, Panel, PanelHeader, Skeleton, StatusDot } from '../components/ui/primitives';
import { EmptyState, ErrorState, Table, TableScroller, THead } from '../components/ui/Table';
import { cn } from '../lib/cn';
import { formatDateTime, formatInteger, formatMinor, formatRelative } from '../lib/format';
import { useCurrencies, useEntries, useIntegrity, useStats, useTransactions } from '../lib/queries';

function StatTile({
  label,
  value,
  meta,
  tone = 'neutral',
  loading,
}: {
  label: string;
  value: string;
  meta?: React.ReactNode;
  tone?: 'neutral' | 'positive' | 'negative';
  loading?: boolean;
}) {
  return (
    <div className="panel px-4 py-3">
      <p className="label">{label}</p>
      {loading ? (
        <Skeleton className="mt-2 h-6 w-24" />
      ) : (
        <p
          className={cn(
            'num mt-1.5 text-2xl font-semibold tracking-tight',
            tone === 'positive' && 'text-positive',
            tone === 'negative' && 'text-negative',
            tone === 'neutral' && 'text-ink',
          )}
        >
          {value}
        </p>
      )}
      {meta && <div className="mt-1.5 text-xs text-ink-3">{meta}</div>}
    </div>
  );
}

function VolumeChart({ currency }: { currency: string }) {
  const { data: stats } = useStats(14);
  const { data: currencies } = useCurrencies();

  const series = useMemo(() => {
    if (!stats) return [];
    const byDay = new Map<string, { day: string; value: number; count: number }>();
    // Fill the whole window so quiet days read as zero rather than vanishing.
    for (let i = 13; i >= 0; i -= 1) {
      const date = new Date();
      date.setUTCHours(0, 0, 0, 0);
      date.setUTCDate(date.getUTCDate() - i);
      const key = date.toISOString().slice(0, 10);
      byDay.set(key, { day: key, value: 0, count: 0 });
    }
    for (const row of stats.daily_volume) {
      if (row.currency !== currency) continue;
      const bucket = byDay.get(row.day.slice(0, 10));
      if (bucket) {
        bucket.value = row.posted.amount_minor;
        bucket.count = row.transaction_count;
      }
    }
    return [...byDay.values()];
  }, [stats, currency]);

  const exponent = currencies?.find((c) => c.code === currency)?.exponent ?? 2;

  return (
    <ResponsiveContainer width="100%" height={188}>
      <BarChart data={series} margin={{ top: 8, right: 4, bottom: 0, left: -8 }} barCategoryGap="28%">
        <CartesianGrid stroke="var(--border)" vertical={false} />
        <XAxis
          dataKey="day"
          tickFormatter={(value: string) => value.slice(8, 10)}
          tick={{ fill: 'var(--ink-3)', fontSize: 11 }}
          axisLine={{ stroke: 'var(--border)' }}
          tickLine={false}
        />
        <YAxis
          tickFormatter={(value: number) =>
            new Intl.NumberFormat('en-US', { notation: 'compact' }).format(value / 10 ** exponent)
          }
          tick={{ fill: 'var(--ink-3)', fontSize: 11 }}
          axisLine={false}
          tickLine={false}
          width={52}
        />
        <Tooltip
          cursor={{ fill: 'var(--surface-3)' }}
          content={({ active, payload, label }) => {
            if (!active || !payload?.length) return null;
            const point = payload[0].payload as { value: number; count: number };
            return (
              <div className="panel px-2.5 py-2 text-xs shadow-lg">
                <p className="num mb-1 text-ink-3">{label}</p>
                <p className="num font-semibold text-ink">
                  {formatMinor(point.value, currency, currencies, true)}
                </p>
                <p className="text-ink-3">
                  {point.count} transaction{point.count === 1 ? '' : 's'}
                </p>
              </div>
            );
          }}
        />
        <Bar dataKey="value" fill="var(--accent)" radius={[2, 2, 0, 0]} maxBarSize={26} />
      </BarChart>
    </ResponsiveContainer>
  );
}

export default function DashboardPage() {
  const stats = useStats(14);
  const integrity = useIntegrity(true);
  const recentTransactions = useTransactions({ limit: 7, offset: 0 });
  const recentEntries = useEntries({ limit: 8, offset: 0 });

  const currenciesInUse = stats.data?.trial_balance.map((row) => row.currency) ?? [];
  const [chartCurrency, setChartCurrency] = useState<string | null>(null);
  const activeCurrency = chartCurrency ?? currenciesInUse[0] ?? 'USD';

  const balanced = stats.data?.ledger_balanced;

  return (
    <>
      <PageHeader
        title="Ledger overview"
        description={
          stats.data
            ? `Derived from ${formatInteger(stats.data.entry_count)} ledger entries · refreshed ${formatRelative(stats.data.generated_at)}`
            : 'Reading the ledger…'
        }
        actions={
          <Button
            variant="secondary"
            onClick={() => integrity.refetch()}
            icon={<ShieldCheck className="h-3.5 w-3.5" strokeWidth={1.8} />}
          >
            Verify integrity
          </Button>
        }
      />

      <div className="space-y-4 px-6 py-5">
        {stats.isError ? (
          <Panel>
            <ErrorState error={stats.error} onRetry={() => stats.refetch()} />
          </Panel>
        ) : (
          <>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <StatTile
                label="Accounts"
                value={stats.data ? formatInteger(stats.data.account_count) : '—'}
                loading={stats.isLoading}
                meta={
                  stats.data && (
                    <span>
                      <span className="num text-ink-2">{stats.data.active_account_count}</span> active
                      across{' '}
                      <span className="num text-ink-2">{stats.data.accounts_by_type.length}</span>{' '}
                      types
                    </span>
                  )
                }
              />
              <StatTile
                label="Transactions posted"
                value={stats.data ? formatInteger(stats.data.transaction_count) : '—'}
                loading={stats.isLoading}
                meta={
                  stats.data && (
                    <span>
                      <span className="num text-ink-2">
                        {stats.data.reversed_transaction_count}
                      </span>{' '}
                      reversed
                    </span>
                  )
                }
              />
              <StatTile
                label="Ledger entries"
                value={stats.data ? formatInteger(stats.data.entry_count) : '—'}
                loading={stats.isLoading}
                meta="Append-only, never edited"
              />
              <StatTile
                label="Trial balance"
                value={balanced === undefined ? '—' : balanced ? 'Balanced' : 'Out of balance'}
                tone={balanced === undefined ? 'neutral' : balanced ? 'positive' : 'negative'}
                loading={stats.isLoading}
                meta={
                  stats.data && (
                    <span className="flex items-center gap-1.5">
                      <StatusDot tone={balanced ? 'positive' : 'negative'} />
                      Σ debits = Σ credits in {stats.data.trial_balance.length} currenc
                      {stats.data.trial_balance.length === 1 ? 'y' : 'ies'}
                    </span>
                  )
                }
              />
            </div>

            <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
              <Panel className="xl:col-span-2">
                <PanelHeader
                  title="Posting volume"
                  description="Value posted per day over the last 14 days"
                  actions={
                    currenciesInUse.length > 1 && (
                      <div className="flex items-center gap-px rounded-sm border border-border p-0.5">
                        {currenciesInUse.map((code) => (
                          <button
                            key={code}
                            type="button"
                            onClick={() => setChartCurrency(code)}
                            className={cn(
                              'num rounded-xs px-2 py-0.5 text-2xs font-semibold transition-colors',
                              code === activeCurrency
                                ? 'bg-accent-soft text-accent-ink'
                                : 'text-ink-3 hover:text-ink',
                            )}
                          >
                            {code}
                          </button>
                        ))}
                      </div>
                    )
                  }
                />
                <div className="px-3 py-3">
                  {stats.isLoading ? (
                    <Skeleton className="h-[188px] w-full" />
                  ) : (
                    <VolumeChart currency={activeCurrency} />
                  )}
                </div>
              </Panel>

              <Panel>
                <PanelHeader
                  title="Trial balance"
                  description="Σ debits vs Σ credits, whole ledger"
                />
                {stats.data && stats.data.trial_balance.length > 0 ? (
                  <TableScroller>
                    <Table>
                      <THead>
                        <th className="th">Currency</th>
                        <th className="th text-right">Debits</th>
                        <th className="th text-right">Credits</th>
                        <th className="th text-right">Δ</th>
                      </THead>
                      <tbody>
                        {stats.data.trial_balance.map((row) => (
                          <tr key={row.currency}>
                            <td className="td num font-medium">{row.currency}</td>
                            <td className="td text-right">
                              <Amount money={row.debits} tone="debit" />
                            </td>
                            <td className="td text-right">
                              <Amount money={row.credits} tone="credit" />
                            </td>
                            <td className="td text-right">
                              {row.balanced ? (
                                <span className="inline-flex items-center gap-1 text-xs text-positive">
                                  <CheckCircle2 className="h-3.5 w-3.5" strokeWidth={2} />
                                  <span className="num">0</span>
                                </span>
                              ) : (
                                <Amount money={row.difference} tone="auto" />
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </Table>
                  </TableScroller>
                ) : (
                  <EmptyState
                    title="No entries yet"
                    description="Post a transaction to populate the trial balance."
                  />
                )}

                <div className="border-t border-border px-4 py-3">
                  <p className="label mb-2">Integrity check</p>
                  {integrity.data ? (
                    <ul className="space-y-1.5 text-xs">
                      <li className="flex items-center justify-between gap-2">
                        <span className="text-ink-3">Cached vs derived balances</span>
                        <span
                          className={cn(
                            'inline-flex items-center gap-1',
                            integrity.data.cache_consistent ? 'text-positive' : 'text-negative',
                          )}
                        >
                          {integrity.data.cache_consistent ? (
                            <ShieldCheck className="h-3.5 w-3.5" strokeWidth={1.9} />
                          ) : (
                            <ShieldAlert className="h-3.5 w-3.5" strokeWidth={1.9} />
                          )}
                          {integrity.data.cache_consistent ? 'consistent' : 'divergent'}
                        </span>
                      </li>
                      <li className="flex items-center justify-between gap-2">
                        <span className="text-ink-3">Accounts checked</span>
                        <span className="num text-ink-2">{integrity.data.accounts_checked}</span>
                      </li>
                      <li className="flex items-center justify-between gap-2">
                        <span className="text-ink-3">Unbalanced transactions</span>
                        <span className="num text-ink-2">
                          {integrity.data.unbalanced_transaction_ids.length}
                        </span>
                      </li>
                      <li className="flex items-center justify-between gap-2">
                        <span className="text-ink-3">Checked</span>
                        <span className="num text-ink-2">
                          {formatRelative(integrity.data.checked_at)}
                        </span>
                      </li>
                    </ul>
                  ) : (
                    <Skeleton className="h-16 w-full" />
                  )}
                </div>
              </Panel>
            </div>

            <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
              <Panel className="xl:col-span-2">
                <PanelHeader
                  title="Recent transactions"
                  actions={
                    <Link
                      to="/transactions"
                      className="inline-flex items-center gap-1 text-xs text-accent hover:underline"
                    >
                      View all <ArrowUpRight className="h-3 w-3" />
                    </Link>
                  }
                />
                {recentTransactions.isError ? (
                  <ErrorState error={recentTransactions.error} />
                ) : recentTransactions.data?.items.length === 0 ? (
                  <EmptyState
                    title="No transactions posted"
                    description="Once a transaction is posted it will appear here with its full entry set."
                  />
                ) : (
                  <TableScroller>
                    <Table>
                      <THead>
                        <th className="th">Reference</th>
                        <th className="th">Description</th>
                        <th className="th">Posted</th>
                        <th className="th text-right">Amount</th>
                        <th className="th">Status</th>
                      </THead>
                      <tbody>
                        {recentTransactions.data?.items.map((transaction) => (
                          <tr key={transaction.id} className="row-link">
                            <td className="td">
                              <Link
                                to={`/transactions/${transaction.id}`}
                                className="num font-medium text-ink hover:text-accent"
                              >
                                {transaction.reference}
                              </Link>
                            </td>
                            <td className="td max-w-[22rem] truncate text-ink-2">
                              {transaction.description}
                            </td>
                            <td className="td whitespace-nowrap text-ink-3">
                              {formatRelative(transaction.posted_at)}
                            </td>
                            <td className="td text-right">
                              <Amount money={transaction.total_debits} showCurrency />
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
                )}
              </Panel>

              <Panel>
                <PanelHeader
                  title="Recent ledger activity"
                  actions={
                    <Link
                      to="/ledger"
                      className="inline-flex items-center gap-1 text-xs text-accent hover:underline"
                    >
                      Explore <ArrowUpRight className="h-3 w-3" />
                    </Link>
                  }
                />
                {recentEntries.data?.items.length === 0 ? (
                  <EmptyState title="The ledger is empty" />
                ) : (
                  <ul className="divide-y divide-border">
                    {recentEntries.data?.items.map((entry) => (
                      <li key={entry.id} className="flex items-center gap-3 px-4 py-2">
                        <DirectionBadge direction={entry.direction} />
                        <div className="min-w-0 flex-1">
                          <AccountRef account={entry.account} />
                        </div>
                        <div className="text-right">
                          <Amount
                            money={entry.amount}
                            tone={entry.direction === 'DEBIT' ? 'debit' : 'credit'}
                          />
                          <p className="text-2xs text-ink-3">{formatDateTime(entry.created_at)}</p>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
                {stats.data && (
                  <div className="flex flex-wrap gap-1.5 border-t border-border px-4 py-3">
                    {stats.data.accounts_by_type.map((row) => (
                      <Badge key={row.type} tone="neutral">
                        {row.type} · {row.count}
                      </Badge>
                    ))}
                  </div>
                )}
              </Panel>
            </div>
          </>
        )}
      </div>
    </>
  );
}
