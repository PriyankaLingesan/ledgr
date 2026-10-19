/**
 * Financial operations dashboard.
 *
 * Every figure comes from `GET /dashboard`, which the server assembles from the
 * ledger, the audit trail and the latest reconciliation run at request time.
 * There are no invented metrics and no decorative numbers: if the server cannot
 * state it, it is not on this page.
 */

import { ArrowUpRight, ShieldAlert, ShieldCheck } from 'lucide-react';
import { useMemo } from 'react';
import { Link } from 'react-router-dom';
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';

import { LedgerBriefWidget } from '../components/ai/LedgerBriefWidget';
import { PageHeader } from '../components/layout/AppShell';
import { Amount, TransactionStatusBadge } from '../components/ledger/atoms';
import {
  AuditActionBadge,
  CaseStatusBadge,
  OnboardingChecklist,
  PriorityBadge,
  SeverityBadge,
} from '../components/ops/atoms';
import { Badge, Button, Panel, PanelHeader, Skeleton, StatusDot } from '../components/ui/primitives';
import { EmptyState, ErrorState, Table, TableScroller, THead } from '../components/ui/Table';
import { useAuth } from '../lib/auth';
import { cn } from '../lib/cn';
import { formatDateTime, formatInteger, formatMinor, formatRelative } from '../lib/format';
import { useDashboard, useLoadDemoData, useOnboarding } from '../lib/opsQueries';
import { useCurrencies } from '../lib/queries';
import { useToast } from '../lib/toast';

function Tile({
  label,
  value,
  meta,
  loading,
  to,
  tone,
}: {
  label: string;
  value: string;
  meta?: React.ReactNode;
  loading?: boolean;
  to?: string;
  tone?: 'warning' | 'negative';
}) {
  const body = (
    <div className="h-full border border-border bg-surface px-5 py-4 transition-colors hover:bg-surface-2">
      <p className="caption">{label}</p>
      {loading ? (
        <Skeleton className="mt-2 h-7 w-20" />
      ) : (
        <p
          className={cn(
            'num mt-1 text-2xl font-semibold',
            tone === 'warning' ? 'text-warning' : tone === 'negative' ? 'text-negative' : 'text-ink',
          )}
        >
          {value}
        </p>
      )}
      {meta && <p className="mt-1 text-[0.8125rem] text-ink-3">{meta}</p>}
    </div>
  );
  return to ? (
    <Link to={to} className="block focus-visible:outline-2 focus-visible:outline-accent">
      {body}
    </Link>
  ) : (
    body
  );
}

function VolumeChart({
  rows,
  currency,
}: {
  rows: { day: string; currency: string; transaction_count: number; posted_minor: number }[];
  currency: string;
}) {
  const { data: currencies } = useCurrencies();

  const series = useMemo(() => {
    const byDay = new Map<string, { day: string; value: number; count: number }>();
    for (let i = 13; i >= 0; i -= 1) {
      const date = new Date();
      date.setUTCHours(0, 0, 0, 0);
      date.setUTCDate(date.getUTCDate() - i);
      const key = date.toISOString().slice(0, 10);
      byDay.set(key, { day: key, value: 0, count: 0 });
    }
    for (const row of rows) {
      if (row.currency !== currency) continue;
      const bucket = byDay.get(row.day.slice(0, 10));
      if (bucket) {
        bucket.value = row.posted_minor;
        bucket.count = row.transaction_count;
      }
    }
    return [...byDay.values()];
  }, [rows, currency]);

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

/** A figure the server did not return is shown as "—", never as a misleading 0. */
const stat = (value: number | undefined) => (value === undefined ? '—' : formatInteger(value));

export default function DashboardPage() {
  const { user, can } = useAuth();
  const toast = useToast();
  const dashboard = useDashboard();
  const onboarding = useOnboarding();
  const loadDemo = useLoadDemoData();
  const data = dashboard.data;

  // Chart the currency that actually has the most activity.
  const currency = useMemo(() => {
    if (!data?.daily_volume.length) return 'USD';
    const totals = new Map<string, number>();
    for (const row of data.daily_volume) {
      totals.set(row.currency, (totals.get(row.currency) ?? 0) + row.transaction_count);
    }
    return [...totals.entries()].sort((a, b) => b[1] - a[1])[0][0];
  }, [data]);

  const alertTotal = data
    ? data.alert_counts.CRITICAL + data.alert_counts.HIGH + data.alert_counts.MEDIUM + data.alert_counts.LOW
    : 0;
  const recon = data?.latest_reconciliation ?? null;
  const integrityOk = data ? data.ledger_balanced && data.integrity_all_passed : null;
  const showChecklist =
    onboarding.data && onboarding.data.completed < onboarding.data.total && can('org:manage');

  return (
    <>
      <PageHeader
        title={`Welcome${user?.name ? `, ${user.name.split('@')[0]}` : ''}`}
        description={
          user?.role === 'AUDITOR'
            ? 'Read-only view of the ledger, reconciliation and audit trail.'
            : 'What needs attention across the ledger, reconciliation and approvals.'
        }
      />

      <div className="space-y-6 px-5 py-6 sm:px-8">
        {dashboard.isError && (
          <Panel>
            <ErrorState error={dashboard.error} onRetry={() => dashboard.refetch()} context="the dashboard" />
          </Panel>
        )}

        <div className="grid grid-cols-2 gap-px bg-border lg:grid-cols-4">
          <Tile
            label="Accounts"
            value={stat(data?.account_count)}
            meta={data && `${formatInteger(data.active_account_count)} active`}
            loading={dashboard.isLoading}
            to="/app/accounts"
          />
          <Tile
            label="Transactions, last 24h"
            value={stat(data?.transactions_last_24h)}
            meta={data && `${formatInteger(data.transaction_count)} all time`}
            loading={dashboard.isLoading}
            to="/app/transactions"
          />
          <Tile
            label="Awaiting approval"
            value={stat(data?.pending_approvals)}
            meta="Maker-checker queue"
            loading={dashboard.isLoading}
            tone={data && data.pending_approvals > 0 ? 'warning' : undefined}
            to={can('drafts:read') ? '/app/approvals' : undefined}
          />
          <Tile
            label="Open investigations"
            value={stat(data?.open_investigations)}
            meta={alertTotal > 0 ? `${alertTotal} active alert${alertTotal === 1 ? '' : 's'}` : 'No active alerts'}
            loading={dashboard.isLoading}
            tone={data && data.open_investigations > 0 ? 'warning' : undefined}
            to="/app/investigations"
          />
        </div>

        {data && (
          <div className="grid gap-6 xl:grid-cols-2">
            <Panel>
              <PanelHeader
                title={`Posted value, last ${data.volume_window_days} days`}
                description="Total debits of non-reversed transactions (equal to total credits), per currency"
              />
              {data.posted_volume.length > 0 ? (
                <ul className="divide-y divide-border">
                  {data.posted_volume.map((row) => (
                    <li key={row.currency} className="flex items-center justify-between px-4 py-3">
                      <span className="text-[0.875rem] text-ink-2">
                        {row.currency} · {formatInteger(row.transaction_count)} transaction
                        {row.transaction_count === 1 ? '' : 's'}
                      </span>
                      <span className="num text-[0.9375rem] font-semibold text-ink">
                        {formatMinor(row.posted_minor, row.currency, undefined, true)}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <EmptyState title="Nothing posted in this window" />
              )}
            </Panel>
            <Panel>
              <PanelHeader
                title="Failed or rejected operations, last 24 hours"
                description="Counted from the audit trail - nothing was posted by any of these"
              />
              <dl className="grid grid-cols-3 gap-px bg-border text-[0.8125rem]">
                {(
                  [
                    ['Postings rejected', data.failed_operations_24h.rejected_postings, '/app/audit?event_type=transaction.post_failed'],
                    ['Approvals refused by ledger', data.failed_operations_24h.refused_approvals, '/app/audit?event_type=draft.approval_failed'],
                    ['Drafts rejected', data.failed_operations_24h.rejected_drafts, '/app/audit?event_type=draft.rejected'],
                  ] as const
                ).map(([label, count, href]) => (
                  <div key={label} className="bg-surface px-4 py-3">
                    <dt className="text-ink-3">{label}</dt>
                    <dd className={cn('num mt-0.5 text-lg font-semibold', count > 0 ? 'text-warning' : 'text-ink')}>
                      {count > 0 && can('audit:read') ? (
                        <Link to={href.replace('/app', '/app')} className="hover:underline">
                          {formatInteger(count)}
                        </Link>
                      ) : (
                        formatInteger(count)
                      )}
                    </dd>
                  </div>
                ))}
              </dl>
            </Panel>
          </div>
        )}

        {showChecklist && onboarding.data && (
          <div className="space-y-3">
            <OnboardingChecklist onboarding={onboarding.data} />
            {!onboarding.data.steps.find((s) => s.key === 'external_data')?.done && (
              <div className="flex flex-wrap items-center gap-3 text-[0.8125rem] text-ink-2">
                <Button
                  size="sm"
                  disabled={loadDemo.isPending}
                  onClick={() =>
                    loadDemo.mutate(undefined, {
                      onSuccess: (result) => toast.success('Demo data loaded', result.message),
                      onError: (error) => toast.apiError(error),
                    })
                  }
                >
                  Load demo reconciliation data
                </Button>
                <span>
                  Creates two demo accounts and a small reconciliation scenario so you can explore
                  without real data.
                </span>
              </div>
            )}
          </div>
        )}

        <div className="grid gap-6 xl:grid-cols-3">
          {/* integrity & health */}
          <Panel>
            <PanelHeader title="Ledger integrity" description="Re-verified from ledger entries on every load" />
            <div className="space-y-3 px-4 py-4">
              {dashboard.isLoading ? (
                <Skeleton className="h-16 w-full" />
              ) : data ? (
                <>
                  <div className="flex items-center gap-3">
                    <span
                      className={cn(
                        'flex h-9 w-9 items-center justify-center rounded-md border',
                        integrityOk
                          ? 'border-positive/30 bg-positive-soft text-positive'
                          : 'border-negative/30 bg-negative-soft text-negative',
                      )}
                    >
                      {integrityOk ? <ShieldCheck className="h-4 w-4" /> : <ShieldAlert className="h-4 w-4" />}
                    </span>
                    <div>
                      <p className="text-[0.9375rem] font-medium text-ink">
                        {integrityOk ? 'Ledger is consistent' : 'Integrity check failing'}
                      </p>
                      <p className="text-[0.8125rem] text-ink-2">
                        Debits {data.ledger_balanced ? 'equal' : 'do NOT equal'} credits across the ledger.
                      </p>
                    </div>
                  </div>
                  {data.integrity_checks_failed.length > 0 && (
                    <ul className="list-inside list-disc text-[0.8125rem] text-negative">
                      {data.integrity_checks_failed.map((name) => (
                        <li key={name}>{name}</li>
                      ))}
                    </ul>
                  )}
                  <div className="flex items-center justify-between border-t border-border pt-3 text-[0.8125rem]">
                    <span className="flex items-center gap-2 text-ink-2">
                      <StatusDot tone={data.database === 'up' ? 'positive' : 'negative'} />
                      Database
                    </span>
                    <span className={data.database === 'up' ? 'text-ink-3' : 'text-negative'}>
                      {data.database === 'up' ? 'Reachable' : 'Unreachable'}
                    </span>
                  </div>
                  {can('system:read') && (
                    <Link
                      to="/app/system"
                      className="inline-flex items-center gap-1 text-[0.8125rem] font-medium text-accent-ink hover:underline"
                    >
                      System health <ArrowUpRight className="h-3 w-3" />
                    </Link>
                  )}
                </>
              ) : null}
            </div>
          </Panel>

          {/* reconciliation */}
          <Panel>
            <PanelHeader
              title="Reconciliation"
              description={recon ? `Run #${recon.run_number}, ${formatRelative(recon.run_at)}` : 'No run yet'}
              actions={
                <Link to="/app/reconciliation" className="text-[0.8125rem] font-medium text-accent-ink hover:underline">
                  Open workspace
                </Link>
              }
            />
            {dashboard.isLoading ? (
              <div className="px-4 py-4">
                <Skeleton className="h-20 w-full" />
              </div>
            ) : recon ? (
              <dl className="grid grid-cols-2 gap-px bg-border text-[0.8125rem]">
                {[
                  ['Matched', recon.matched, 'text-positive'],
                  ['Amount mismatch', recon.mismatched, recon.mismatched ? 'text-negative' : 'text-ink'],
                  ['Missing in ledger', recon.missing_internal, recon.missing_internal ? 'text-warning' : 'text-ink'],
                  ['Missing externally', recon.missing_external, recon.missing_external ? 'text-warning' : 'text-ink'],
                  ['Ambiguous', recon.ambiguous, recon.ambiguous ? 'text-accent-ink' : 'text-ink'],
                  ['External records', recon.external_count, 'text-ink'],
                ].map(([label, value, tone]) => (
                  <div key={label as string} className="bg-surface px-4 py-3">
                    <dt className="text-ink-3">{label}</dt>
                    <dd className={cn('num mt-0.5 text-lg font-semibold', tone as string)}>
                      {formatInteger(value as number)}
                    </dd>
                  </div>
                ))}
              </dl>
            ) : (
              <EmptyState
                title="No reconciliation has been run"
                description="Import external records and run a reconciliation to compare them with the ledger."
              />
            )}
          </Panel>

          {/* alerts */}
          <Panel>
            <PanelHeader
              title="Alerts"
              description={alertTotal ? `${alertTotal} active` : 'Nothing needs attention'}
              actions={
                can('alerts:read') && (
                  <Link to="/app/alerts" className="text-[0.8125rem] font-medium text-accent-ink hover:underline">
                    View all
                  </Link>
                )
              }
            />
            {dashboard.isLoading ? (
              <div className="px-4 py-4">
                <Skeleton className="h-20 w-full" />
              </div>
            ) : data && data.top_alerts.length > 0 ? (
              <ul className="divide-y divide-border">
                {data.top_alerts.map((alert) => (
                  <li key={alert.id} className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <SeverityBadge severity={alert.severity} />
                      <span className="truncate text-[0.875rem] font-medium text-ink">{alert.title}</span>
                    </div>
                    <p className="mt-1 line-clamp-2 text-[0.8125rem] text-ink-2">{alert.detail}</p>
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState
                title="No active alerts"
                description="Alerts are deterministic rules over real activity - retries, failed postings, large or repeated reversals, reconciliation exceptions."
              />
            )}
          </Panel>
        </div>

        <div className="grid gap-6 xl:grid-cols-3">
          <Panel className="xl:col-span-2">
            <PanelHeader
              title="Posted value, last 14 days"
              description={`${currency} - total debits of non-reversed transactions`}
            />
            <div className="px-3 py-3">
              {dashboard.isLoading ? (
                <Skeleton className="h-44 w-full" />
              ) : data && data.daily_volume.length > 0 ? (
                <VolumeChart rows={data.daily_volume} currency={currency} />
              ) : (
                <EmptyState
                  title="No activity in the last 14 days"
                  description="Posted transactions will appear here."
                />
              )}
            </div>
          </Panel>

          {can('ai:use') ? (
            <LedgerBriefWidget />
          ) : (
            <Panel>
              <PanelHeader
                title="Open investigations"
                description="Most recent cases that are not resolved"
              />
              {data && data.recent_investigations.length > 0 ? (
                <ul className="divide-y divide-border">
                  {data.recent_investigations.map((c) => (
                    <li key={c.id} className="px-4 py-3">
                      <Link to={`/app/investigations/${c.id}`} className="block hover:underline">
                        <span className="num text-2xs text-ink-3">{c.case_number}</span>
                        <p className="truncate text-[0.875rem] font-medium text-ink">{c.title}</p>
                      </Link>
                      <div className="mt-1 flex items-center gap-1.5">
                        <CaseStatusBadge status={c.status} />
                        <PriorityBadge priority={c.priority} />
                      </div>
                    </li>
                  ))}
                </ul>
              ) : (
                <EmptyState title="No open investigations" />
              )}
            </Panel>
          )}
        </div>

        <div className="grid gap-6 xl:grid-cols-2">
          <Panel>
            <PanelHeader
              title="Recent transactions"
              actions={
                <Link to="/app/transactions" className="text-[0.8125rem] font-medium text-accent-ink hover:underline">
                  All transactions
                </Link>
              }
            />
            {dashboard.isLoading ? (
              <div className="px-4 py-4">
                <Skeleton className="h-32 w-full" />
              </div>
            ) : data && data.recent_transactions.length > 0 ? (
              <TableScroller>
                <Table>
                  <THead>
                    <th className="px-4 py-2 text-xs font-medium text-ink-3">Reference</th>
                    <th className="px-4 py-2 text-xs font-medium text-ink-3">Posted</th>
                    <th className="px-4 py-2 text-right text-xs font-medium text-ink-3">Amount</th>
                    <th className="px-4 py-2 text-xs font-medium text-ink-3">Status</th>
                  </THead>
                  <tbody className="divide-y divide-border">
                    {data.recent_transactions.map((t) => (
                      <tr key={t.id} className="hover:bg-surface-2">
                        <td className="px-4 py-2">
                          <Link to={`/app/transactions/${t.id}`} className="num text-[0.8125rem] text-ink hover:underline">
                            {t.reference}
                          </Link>
                          <p className="max-w-[16rem] truncate text-xs text-ink-3">{t.description}</p>
                        </td>
                        <td className="px-4 py-2 text-[0.8125rem] text-ink-2">{formatDateTime(t.posted_at)}</td>
                        <td className="px-4 py-2 text-right">
                          <Amount money={t.amount} showCurrency />
                        </td>
                        <td className="px-4 py-2">
                          <TransactionStatusBadge status={t.status as 'POSTED' | 'REVERSED'} kind={t.kind as 'STANDARD' | 'REVERSAL'} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              </TableScroller>
            ) : (
              <EmptyState
                title="No transactions yet"
                description="Post your first double-entry transaction to see activity here."
                action={
                  (can('transactions:post') || can('drafts:create')) && (
                    <Link to="/app/transactions/new">
                      <Button variant="primary" size="sm">
                        New transaction
                      </Button>
                    </Link>
                  )
                }
              />
            )}
          </Panel>

          <Panel>
            <PanelHeader
              title="Recent audit activity"
              description="Real, append-only events"
              actions={
                can('audit:read') && (
                  <Link to="/app/audit" className="text-[0.8125rem] font-medium text-accent-ink hover:underline">
                    Audit log
                  </Link>
                )
              }
            />
            {dashboard.isLoading ? (
              <div className="px-4 py-4">
                <Skeleton className="h-32 w-full" />
              </div>
            ) : data && data.recent_audit.length > 0 ? (
              <ul className="divide-y divide-border">
                {data.recent_audit.map((event) => (
                  <li key={event.id} className="flex items-center justify-between gap-3 px-4 py-2.5">
                    <div className="min-w-0">
                      <AuditActionBadge eventType={event.event_type} />
                      <p className="mt-1 truncate text-xs text-ink-3">
                        {event.actor} · {formatDateTime(event.created_at)}
                      </p>
                    </div>
                    {event.resource_type && (
                      <Badge tone="neutral" className="shrink-0">
                        {event.resource_type}
                      </Badge>
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState title="No audit activity yet" />
            )}
          </Panel>
        </div>
      </div>
    </>
  );
}
