/**
 * Operations inbox: the one place that lists what is waiting for a person.
 *
 * Four real queues, each linking to the underlying record: approvals awaiting a
 * second person, unresolved reconciliation exceptions that nobody has opened a
 * case or recorded a decision for, investigations still open, and active
 * alerts. Queues the signed-in role cannot read are not shown.
 */

import { Inbox } from 'lucide-react';
import { Link } from 'react-router-dom';

import { PageHeader } from '../components/layout/AppShell';
import { Amount } from '../components/ledger/atoms';
import { CaseStatusBadge, PriorityBadge, RECON_LABELS, ReconStatusBadge, SeverityBadge } from '../components/ops/atoms';
import { Badge, Panel, PanelHeader, Skeleton } from '../components/ui/primitives';
import { EmptyState, ErrorState } from '../components/ui/Table';
import { useAuth } from '../lib/auth';
import { formatRelative } from '../lib/format';
import { useAlerts, useDrafts } from '../lib/opsQueries';
import { useInvestigations, useReconciliationRun, useReconciliationRuns } from '../lib/queries';

function Queue({
  title,
  description,
  count,
  to,
  loading,
  error,
  onRetry,
  empty,
  children,
}: {
  title: string;
  description: string;
  count: number | undefined;
  to: string;
  loading: boolean;
  error: unknown;
  onRetry: () => void;
  empty: string;
  children: React.ReactNode;
}) {
  return (
    <Panel>
      <PanelHeader
        title={
          <span className="inline-flex items-center gap-2">
            {title}
            {count !== undefined && count > 0 && <Badge tone="warning">{count}</Badge>}
          </span>
        }
        description={description}
        actions={
          <Link to={to} className="text-[0.8125rem] font-medium text-accent-ink hover:underline">
            Open
          </Link>
        }
      />
      {error ? (
        <ErrorState error={error} onRetry={onRetry} context={title.toLowerCase()} />
      ) : loading ? (
        <div className="p-4">
          <Skeleton className="h-20 w-full" />
        </div>
      ) : count === 0 ? (
        <EmptyState title={empty} />
      ) : (
        <ul className="divide-y divide-border">{children}</ul>
      )}
    </Panel>
  );
}

export default function InboxPage() {
  const { can } = useAuth();

  const approvals = useDrafts({ status: 'PENDING_APPROVAL', limit: 8 });
  const openCases = useInvestigations({ status: 'OPEN', limit: 8 });
  const activeCases = useInvestigations({ status: 'INVESTIGATING', limit: 8 });
  const alerts = useAlerts();
  const runs = useReconciliationRuns({ limit: 1 });
  const latestRunId = runs.data?.items[0]?.id;
  const run = useReconciliationRun(latestRunId);

  const exceptions = (run.data?.results ?? []).filter(
    (r) => r.status !== 'MATCHED' && !r.investigation_id && !r.latest_decision,
  );
  const unresolvedCases = [...(openCases.data?.items ?? []), ...(activeCases.data?.items ?? [])];
  const unresolvedTotal = (openCases.data?.total ?? 0) + (activeCases.data?.total ?? 0);

  return (
    <>
      <PageHeader
        title="Operations inbox"
        description="What is waiting for a person right now - each item links to its record"
      />
      <div className="grid gap-6 px-5 py-6 sm:px-8 xl:grid-cols-2">
        {can('drafts:read') && (
          <Queue
            title="Awaiting approval"
            description="Drafts a second person must approve or reject"
            count={approvals.data?.total}
            to="/app/approvals"
            loading={approvals.isLoading}
            error={approvals.error}
            onRetry={() => approvals.refetch()}
            empty="Nothing is waiting for approval"
          >
            {approvals.data?.items.map((d) => (
              <li key={d.id} className="flex items-center justify-between gap-3 px-4 py-3">
                <div className="min-w-0">
                  <Link to="/app/approvals" className="block truncate text-[0.875rem] font-medium text-ink hover:underline">
                    #{d.seq} · {d.description}
                  </Link>
                  <p className="text-xs text-ink-3">
                    {d.created_by} · {formatRelative(d.submitted_at ?? d.created_at)}
                  </p>
                </div>
                <Amount money={d.total} showCurrency />
              </li>
            ))}
          </Queue>
        )}

        {can('reconciliation:read') && (
          <Queue
            title="Reconciliation exceptions"
            description="From the latest run, with no case and no recorded decision yet"
            count={run.data ? exceptions.length : undefined}
            to="/app/reconciliation"
            loading={runs.isLoading || run.isLoading}
            error={runs.error ?? run.error}
            onRetry={() => (runs.isError ? runs.refetch() : run.refetch())}
            empty={latestRunId ? 'Every exception has been picked up' : 'No reconciliation has been run yet'}
          >
            {exceptions.slice(0, 8).map((r) => (
              <li key={r.id} className="flex items-center justify-between gap-3 px-4 py-3">
                <div className="min-w-0">
                  <Link to="/app/reconciliation" className="num block truncate text-[0.875rem] font-medium text-ink hover:underline">
                    {r.external_transaction?.external_id ?? r.internal_reference}
                  </Link>
                  <p className="text-xs text-ink-3">{RECON_LABELS[r.status]}</p>
                </div>
                <span className="flex items-center gap-2">
                  <Amount money={r.difference ?? r.external_transaction?.amount ?? r.internal_amount} showCurrency tone="auto" />
                  <ReconStatusBadge status={r.status} />
                </span>
              </li>
            ))}
          </Queue>
        )}

        {can('investigations:read') && (
          <Queue
            title="Unresolved investigations"
            description="Open or in progress"
            count={openCases.data && activeCases.data ? unresolvedTotal : undefined}
            to="/app/investigations"
            loading={openCases.isLoading || activeCases.isLoading}
            error={openCases.error ?? activeCases.error}
            onRetry={() => {
              openCases.refetch();
              activeCases.refetch();
            }}
            empty="No unresolved investigations"
          >
            {unresolvedCases.slice(0, 8).map((c) => (
              <li key={c.id} className="flex items-center justify-between gap-3 px-4 py-3">
                <div className="min-w-0">
                  <Link to={`/app/investigations/${c.id}`} className="block truncate text-[0.875rem] font-medium text-ink hover:underline">
                    <span className="num text-ink-3">{c.case_number}</span> {c.title}
                  </Link>
                  <p className="text-xs text-ink-3">opened {formatRelative(c.created_at)}</p>
                </div>
                <span className="flex items-center gap-1.5">
                  <PriorityBadge priority={c.priority} />
                  <CaseStatusBadge status={c.status} />
                </span>
              </li>
            ))}
          </Queue>
        )}

        {can('alerts:read') && (
          <Queue
            title="Active alerts"
            description="Deterministic rules over recent activity"
            count={alerts.data?.alerts.length}
            to="/app/alerts"
            loading={alerts.isLoading}
            error={alerts.error}
            onRetry={() => alerts.refetch()}
            empty="No active alerts"
          >
            {alerts.data?.alerts.slice(0, 8).map((a) => (
              <li key={a.id} className="px-4 py-3">
                <div className="flex items-center gap-2">
                  <SeverityBadge severity={a.severity} />
                  <Link to={a.link ? `/app${a.link}` : '/app/alerts'} className="truncate text-[0.875rem] font-medium text-ink hover:underline">
                    {a.title}
                  </Link>
                </div>
                <p className="mt-1 line-clamp-2 text-xs text-ink-2">{a.detail}</p>
              </li>
            ))}
          </Queue>
        )}

        {!can('drafts:read') && !can('reconciliation:read') && !can('investigations:read') && !can('alerts:read') && (
          <Panel className="xl:col-span-2">
            <EmptyState icon={<Inbox className="h-4 w-4" strokeWidth={1.6} />} title="Nothing to show for your role" />
          </Panel>
        )}
      </div>
    </>
  );
}
