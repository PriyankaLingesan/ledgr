/**
 * Ledger overview.
 *
 * Every number here is derived from ledger entries by the API. There are no
 * invented metrics, no growth percentages against imaginary baselines, and
 * no vanity counters - if a figure cannot be read from the API, it is not on
 * this page.
 */

import { ArrowUpRight, ShieldCheck } from 'lucide-react';
import { useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';

import { LedgerBriefWidget } from '../components/ai/LedgerBriefWidget';
import { PageHeader } from '../components/layout/AppShell';
import { Amount, TransactionStatusBadge } from '../components/ledger/atoms';
import { Button, Panel, PanelHeader, Skeleton, StatusDot } from '../components/ui/primitives';
import { EmptyState, ErrorState, Table, TableScroller, THead } from '../components/ui/Table';
import { cn } from '../lib/cn';
import { formatDate, formatInteger, formatMinor } from '../lib/format';
import { useCurrencies, useIntegrity, useStats, useTransactions } from '../lib/queries';

function StatTile({
  label,
  value,
  meta,
  loading,
}: {
  label: string;
  value: string;
  meta?: React.ReactNode;
  loading?: boolean;
}) {
  return (
    <div className="hover-lift relative z-0 hover:z-10 border border-border bg-surface px-5 py-4">
      <p className="caption">{label}</p>
      {loading ? (
        <Skeleton className="mt-2 h-7 w-20" />
      ) : (
        <p className="num mt-1 text-2xl font-semibold text-ink">{value}</p>
      )}
      {meta && <p className="mt-1 text-[0.8125rem] text-ink-3">{meta}</p>}
    </div>
  );
}

function VolumeChart({ currency }: { currency: string }) {
  const { data: stats } = useStats(14);
  const { data: currencies } = useCurrencies();

  const series = useMemo(() => {
    if (!stats) return [];
    const byDay = new Map<string, { day: string; value: number; count: number }>();
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
    <ResponsiveContainer width="100%" height={180}>
      <BarChart data={series} margin={{ top: 8, right: 4, bottom: 0, left: -8 }} barCategoryGap="30%">
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
          width={48}
        />
        <Tooltip
          cursor={{ fill: 'var(--surface-2)' }}
          content={({ active, payload, label }) => {
            if (!active || !payload?.length) return null;
            const point = payload[0].payload as { value: number; count: number };
            return (
              <div className="panel px-3 py-2 text-[0.8125rem] shadow-lg">
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
        <Bar dataKey="value" fill="var(--accent)" radius={[2, 2, 0, 0]} maxBarSize={24} />
      </BarChart>
    </ResponsiveContainer>
  );
}

export default function DashboardPage() {
  const navigate = useNavigate();
  const stats = useStats(14);
  const integrity = useIntegrity(true);
  const recentTransactions = useTransactions({ limit: 6, offset: 0 });

  const currenciesInUse = stats.data?.trial_balance.map((row) => row.currency) ?? [];
  const [chartCurrency, setChartCurrency] = useState<string | null>(null);
  const activeCurrency = chartCurrency ?? currenciesInUse[0] ?? 'USD';
  const balanced = stats.data?.ledger_balanced;

  return (
    <>
      <PageHeader
        title="Dashboard"
        description="A summary of the ledger, drawn directly from posted transactions and entries."
        actions={
          <Button onClick={() => integrity.refetch()} icon={<ShieldCheck className="h-4 w-4" strokeWidth={1.8} />}>
            Verify integrity
          </Button>
        }
      />

      <div className="space-y-6 px-5 py-6 sm:px-8">
        {stats.isError ? (
          <Panel>
            <ErrorState error={stats.error} onRetry={() => stats.refetch()} context="the dashboard" />
          </Panel>
        ) : (
          <>
            <div className="grid grid-cols-1 gap-px border border-border bg-border sm:grid-cols-2 xl:grid-cols-4">
              <StatTile
                label="Total accounts"
                value={stats.data ? formatInteger(stats.data.account_count) : '—'}
                loading={stats.isLoading}
                meta={stats.data && `${stats.data.active_account_count} active`}
              />
              <StatTile
                label="Transactions posted"
                value={stats.data ? formatInteger(stats.data.transaction_count) : '—'}
                loading={stats.isLoading}
                meta={stats.data && `${stats.data.reversed_transaction_count} reversed`}
              />
              <StatTile
                label="Ledger entries"
                value={stats.data ? formatInteger(stats.data.entry_count) : '—'}
                loading={stats.isLoading}
                meta="Append-only"
              />
              <div className="hover-lift relative z-0 hover:z-10 border border-border bg-surface px-5 py-4">
                <p className="caption">System status</p>
                <p
                  className={cn(
                    'mt-1 flex items-center gap-2 text-2xl font-semibold',
                    balanced === undefined ? 'text-ink-3' : balanced ? 'text-positive' : 'text-negative',
                  )}
                >
                  <StatusDot tone={balanced === undefined ? 'neutral' : balanced ? 'positive' : 'negative'} />
                  {balanced === undefined ? '—' : balanced ? 'Balanced' : 'Unbalanced'}
                </p>
                <p className="mt-1 text-[0.8125rem] text-ink-3">
                  {stats.data ? `${stats.data.trial_balance.length} currencies checked` : ' '}
                </p>
              </div>
            </div>

            <LedgerBriefWidget />

            <div className="grid grid-cols-1 gap-6 xl:grid-cols-5">
              <Panel className="xl:col-span-3">
                <PanelHeader
                  title="Posting volume"
                  description="Value posted per day, last 14 days"
                  actions={
                    currenciesInUse.length > 1 && (
                      <div className="flex items-center gap-1">
                        {currenciesInUse.map((code) => (
                          <button
                            key={code}
                            type="button"
                            onClick={() => setChartCurrency(code)}
                            className={cn(
                              'num rounded-xs px-2 py-1 text-[0.8125rem] transition-colors',
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
                <div className="px-4 py-4">
                  {stats.isLoading ? <Skeleton className="h-[180px] w-full" /> : <VolumeChart currency={activeCurrency} />}
                </div>
              </Panel>

              <Panel className="xl:col-span-2">
                <PanelHeader title="Trial balance" description="Debits vs. credits, whole ledger" />
                {stats.data && stats.data.trial_balance.length > 0 ? (
                  <div className="divide-y divide-border">
                    {stats.data.trial_balance.map((row) => (
                      <div key={row.currency} className="flex items-center justify-between px-4 py-3">
                        <span className="num text-[0.9375rem] font-medium text-ink">{row.currency}</span>
                        <div className="text-right">
                          <p className="num text-[0.875rem] text-ink">
                            {formatMinor(row.debits.amount_minor, row.currency)} /{' '}
                            {formatMinor(row.credits.amount_minor, row.currency)}
                          </p>
                          <p
                            className={cn(
                              'text-[0.8125rem]',
                              row.balanced ? 'text-positive' : 'text-negative',
                            )}
                          >
                            {row.balanced ? 'Balanced' : 'Out of balance'}
                          </p>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <EmptyState title="No entries yet" description="Post a transaction to see the trial balance." />
                )}

                {integrity.data && (
                  <div className="border-t border-border px-4 py-3">
                    <p className="flex items-center justify-between text-[0.8125rem]">
                      <span className="text-ink-3">Cached balances vs. ledger entries</span>
                      <span className={integrity.data.cache_consistent ? 'text-positive' : 'text-negative'}>
                        {integrity.data.cache_consistent ? 'Consistent' : 'Divergent'}
                      </span>
                    </p>
                  </div>
                )}
              </Panel>
            </div>

            <Panel>
              <PanelHeader
                title="Recent transactions"
                actions={
                  <Link
                    to="/app/transactions"
                    className="inline-flex items-center gap-1 text-[0.8125rem] text-accent-ink hover:underline"
                  >
                    View all <ArrowUpRight className="h-3.5 w-3.5" />
                  </Link>
                }
              />
              {recentTransactions.isError ? (
                <ErrorState error={recentTransactions.error} context="transactions" />
              ) : recentTransactions.isLoading ? (
                <div className="space-y-3 p-4">
                  {Array.from({ length: 4 }).map((_, i) => (
                    <Skeleton key={i} className="h-5 w-full" />
                  ))}
                </div>
              ) : recentTransactions.data?.items.length === 0 ? (
                <EmptyState
                  title="No transactions yet"
                  description="Your posted transactions will appear here."
                  action={
                    <Button variant="primary" size="sm" onClick={() => navigate('/app/transactions/new')}>
                      Create transaction
                    </Button>
                  }
                />
              ) : (
                <TableScroller>
                  <Table>
                    <THead>
                      <th className="th">Description</th>
                      <th className="th">Date</th>
                      <th className="th text-right">Amount</th>
                      <th className="th">Status</th>
                    </THead>
                    <tbody>
                      {recentTransactions.data?.items.map((transaction) => (
                        <tr key={transaction.id} className="row-link">
                          <td className="td">
                            <Link
                              to={`/app/transactions/${transaction.id}`}
                              className="font-medium text-ink hover:text-accent-ink"
                            >
                              {transaction.description}
                            </Link>
                            <span className="num ml-2 text-[0.8125rem] text-ink-3">
                              {transaction.reference}
                            </span>
                          </td>
                          <td className="td whitespace-nowrap text-ink-2">
                            {formatDate(transaction.posted_at)}
                          </td>
                          <td className="td text-right">
                            <Amount money={transaction.total_debits} showCurrency />
                          </td>
                          <td className="td">
                            <TransactionStatusBadge status={transaction.status} kind={transaction.kind} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </Table>
                </TableScroller>
              )}
            </Panel>
          </>
        )}
      </div>
    </>
  );
}
