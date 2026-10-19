/**
 * System health.
 *
 * Each component's status is checked by the server when this page loads: a
 * timed database round trip, the applied schema revision, the same integrity
 * checks the Control Center runs, the latest reconciliation outcome and the
 * server errors this process has actually recorded. Nothing here is a static
 * "all green" - if a check fails it shows as failing.
 */

import { CheckCircle2, XCircle } from 'lucide-react';
import { Link } from 'react-router-dom';

import { PageHeader } from '../components/layout/AppShell';
import { CopyableId } from '../components/ledger/atoms';
import { Badge, Button, Panel, PanelHeader, Skeleton, StatusDot } from '../components/ui/primitives';
import { EmptyState, ErrorState, Table, TableScroller, THead } from '../components/ui/Table';
import { formatDateTime, formatRelative } from '../lib/format';
import { useSystemHealth } from '../lib/opsQueries';
import type { ComponentStatus } from '../lib/opsTypes';

function tone(status: string): 'positive' | 'negative' | 'warning' | 'neutral' {
  if (status === 'ok') return 'positive';
  if (status === 'down' || status === 'failing') return 'negative';
  if (status === 'attention') return 'warning';
  return 'neutral';
}

function StatusCard({
  title,
  status,
  extra,
}: {
  title: string;
  status: ComponentStatus | undefined;
  extra?: React.ReactNode;
}) {
  return (
    <div className="bg-surface px-5 py-4">
      <p className="caption">{title}</p>
      {status ? (
        <>
          <p className="mt-1.5 flex items-center gap-2 text-[0.9375rem] font-medium text-ink">
            <StatusDot tone={tone(status.status)} pulse={status.status === 'ok'} />
            {status.status === 'ok'
              ? 'Healthy'
              : status.status === 'none'
                ? 'No data yet'
                : status.status === 'attention'
                  ? 'Needs attention'
                  : status.status === 'down'
                    ? 'Down'
                    : 'Failing'}
          </p>
          <p className="mt-1 text-[0.8125rem] text-ink-2">{status.detail}</p>
          {extra}
        </>
      ) : (
        <Skeleton className="mt-2 h-10 w-full" />
      )}
    </div>
  );
}

export default function SystemHealthPage() {
  const query = useSystemHealth();
  const data = query.data;

  return (
    <>
      <PageHeader
        title="System health"
        description={data ? `Checked ${formatRelative(data.generated_at)}` : 'Live component checks'}
        actions={
          <Button size="sm" onClick={() => query.refetch()} disabled={query.isFetching}>
            Check again
          </Button>
        }
      />

      <div className="space-y-6 px-5 py-6 sm:px-8">
        {query.isError && (
          <Panel>
            <ErrorState error={query.error} onRetry={() => query.refetch()} context="system health" />
          </Panel>
        )}

        <div className="grid gap-px bg-border sm:grid-cols-2 xl:grid-cols-4">
          <StatusCard title="API" status={data?.api} />
          <StatusCard
            title="Database"
            status={data?.database}
            extra={
              data && (
                <p className="num mt-1 text-xs text-ink-3">
                  schema revision {data.schema_revision ?? 'unknown'}
                </p>
              )
            }
          />
          <StatusCard title="Ledger integrity" status={data?.ledger_integrity} />
          <StatusCard
            title="Reconciliation"
            status={data?.reconciliation}
            extra={
              data?.latest_reconciliation_at && (
                <Link to="/app/reconciliation" className="mt-1 inline-block text-xs font-medium text-accent-ink hover:underline">
                  Open workspace
                </Link>
              )
            }
          />
        </div>

        <div className="grid gap-6 xl:grid-cols-2">
          <Panel>
            <PanelHeader
              title="Integrity checks"
              description="Each one re-verifies real ledger data when this page loads"
            />
            {data ? (
              <ul className="divide-y divide-border">
                {data.integrity_checks.map((check) => (
                  <li key={check.name} className="flex items-start gap-3 px-4 py-3">
                    {check.passed ? (
                      <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-positive" strokeWidth={1.8} />
                    ) : (
                      <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-negative" strokeWidth={1.8} />
                    )}
                    <div className="min-w-0">
                      <p className="text-[0.875rem] font-medium text-ink">{check.name}</p>
                      <p className="text-[0.8125rem] text-ink-2">{check.detail}</p>
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <div className="p-4">
                <Skeleton className="h-24 w-full" />
              </div>
            )}
          </Panel>

          <Panel>
            <PanelHeader
              title="Requests since this API process started"
              description="In-process counters; reset when the service restarts"
            />
            {data ? (
              Object.keys(data.requests_by_status_class).length > 0 ? (
                <dl className="grid grid-cols-2 gap-px bg-border sm:grid-cols-5">
                  {Object.entries(data.requests_by_status_class)
                    .sort(([a], [b]) => a.localeCompare(b))
                    .map(([klass, count]) => (
                      <div key={klass} className="bg-surface px-4 py-3">
                        <dt className="text-xs text-ink-3">{klass}</dt>
                        <dd
                          className={
                            klass === '5xx' && count > 0
                              ? 'num mt-0.5 text-lg font-semibold text-negative'
                              : 'num mt-0.5 text-lg font-semibold text-ink'
                          }
                        >
                          {count}
                        </dd>
                      </div>
                    ))}
                </dl>
              ) : (
                <EmptyState title="No requests recorded yet" />
              )
            ) : (
              <div className="p-4">
                <Skeleton className="h-16 w-full" />
              </div>
            )}
          </Panel>
        </div>

        <Panel>
          <PanelHeader
            title="Recent server errors"
            description="5xx responses and unexpected exceptions this process has seen. Client mistakes (4xx) are not listed."
          />
          {data && data.recent_errors.length > 0 ? (
            <TableScroller>
              <Table>
                <THead>
                  <th className="px-4 py-2 text-xs font-medium text-ink-3">When</th>
                  <th className="px-4 py-2 text-xs font-medium text-ink-3">Status</th>
                  <th className="px-4 py-2 text-xs font-medium text-ink-3">Code</th>
                  <th className="px-4 py-2 text-xs font-medium text-ink-3">Path</th>
                  <th className="px-4 py-2 text-xs font-medium text-ink-3">Request</th>
                </THead>
                <tbody className="divide-y divide-border">
                  {data.recent_errors.map((error, index) => (
                    <tr key={`${error.at}-${index}`}>
                      <td className="num whitespace-nowrap px-4 py-2 text-[0.8125rem] text-ink-2">
                        {formatDateTime(error.at)}
                      </td>
                      <td className="px-4 py-2">
                        <Badge tone="negative">{error.status_code}</Badge>
                      </td>
                      <td className="num px-4 py-2 text-[0.8125rem] text-ink">{error.code}</td>
                      <td className="num px-4 py-2 text-[0.8125rem] text-ink-2">{error.path}</td>
                      <td className="px-4 py-2">
                        {error.request_id ? <CopyableId value={error.request_id} /> : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            </TableScroller>
          ) : (
            <EmptyState
              icon={<CheckCircle2 className="h-4 w-4" strokeWidth={1.6} />}
              title="No server errors recorded"
              description="Since this API process started, no request has failed with a server-side error."
            />
          )}
        </Panel>
      </div>
    </>
  );
}
