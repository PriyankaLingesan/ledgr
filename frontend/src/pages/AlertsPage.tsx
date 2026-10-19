/**
 * Operational alerts.
 *
 * Deterministic rules over real activity, computed by the server on each load.
 * An alert is informational: it names the rule that fired, the evidence behind
 * it and where to look - it cannot change the ledger, and none of it is
 * produced by AI.
 */

import { BellRing, ExternalLink } from 'lucide-react';
import { Link } from 'react-router-dom';

import { PageHeader } from '../components/layout/AppShell';
import { SeverityBadge } from '../components/ops/atoms';
import { Badge, Button, Panel, Skeleton } from '../components/ui/primitives';
import { EmptyState, ErrorState } from '../components/ui/Table';
import { formatRelative } from '../lib/format';
import { useAlerts } from '../lib/opsQueries';
import type { AlertSeverity } from '../lib/opsTypes';

const SEVERITIES: AlertSeverity[] = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'];

const RULE_NAMES: Record<string, string> = {
  repeated_retries: 'Repeated retries',
  idempotency_conflicts: 'Idempotency conflicts',
  failed_postings: 'Failed postings',
  large_transaction: 'Large transaction',
  repeated_reversals: 'Repeated reversals',
  reconciliation_exceptions: 'Reconciliation',
  balance_floor: 'Balance floor',
  ledger_integrity: 'Ledger integrity',
  repeated_login_failures: 'Sign-in failures',
  permission_denials: 'Permission denials',
};

export default function AlertsPage() {
  const query = useAlerts();
  const data = query.data;

  return (
    <>
      <PageHeader
        title="Alerts"
        description={
          data
            ? `Rules evaluated over the last ${data.window_hours} hours of real activity`
            : 'Deterministic operational alerts'
        }
        actions={
          <Button size="sm" onClick={() => query.refetch()} disabled={query.isFetching}>
            Re-evaluate
          </Button>
        }
      />

      <div className="space-y-6 px-5 py-6 sm:px-8">
        <div className="grid grid-cols-2 gap-px bg-border sm:grid-cols-4">
          {SEVERITIES.map((severity) => (
            <div key={severity} className="bg-surface px-5 py-4">
              <p className="caption">{severity}</p>
              {query.isLoading ? (
                <Skeleton className="mt-2 h-7 w-10" />
              ) : (
                <p className="num mt-1 text-2xl font-semibold text-ink">{data?.counts[severity] ?? 0}</p>
              )}
            </div>
          ))}
        </div>

        <Panel>
          {query.isError ? (
            <ErrorState error={query.error} onRetry={() => query.refetch()} context="alerts" />
          ) : query.isLoading ? (
            <div className="space-y-3 p-4">
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-16 w-full" />
            </div>
          ) : data && data.alerts.length > 0 ? (
            <ul className="divide-y divide-border">
              {data.alerts.map((alert) => (
                <li key={alert.id} className="flex flex-wrap items-start justify-between gap-3 px-4 py-4">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <SeverityBadge severity={alert.severity} />
                      <h3 className="text-[0.9375rem] font-medium text-ink">{alert.title}</h3>
                      <Badge tone="neutral">{RULE_NAMES[alert.rule] ?? alert.rule}</Badge>
                    </div>
                    <p className="mt-1.5 text-[0.875rem] leading-relaxed text-ink-2">{alert.detail}</p>
                    <p className="mt-1.5 text-xs text-ink-3">
                      {alert.count > 1 && <span className="num">{alert.count} occurrences · </span>}
                      {alert.last_seen ? `last ${formatRelative(alert.last_seen)}` : 'current state'}
                    </p>
                  </div>
                  {alert.link && (
                    <Link
                      to={`/app${alert.link}`}
                      className="inline-flex items-center gap-1 text-[0.8125rem] font-medium text-accent-ink hover:underline"
                    >
                      Investigate <ExternalLink className="h-3 w-3" />
                    </Link>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState
              icon={<BellRing className="h-4 w-4" strokeWidth={1.6} />}
              title="No active alerts"
              description="Nothing is tripping a rule right now. Alerts appear for retry storms, rejected postings, large or repeated reversals, accounts near their balance floor and reconciliation exceptions."
            />
          )}
        </Panel>
      </div>
    </>
  );
}
