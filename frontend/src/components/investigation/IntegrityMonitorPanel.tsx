/**
 * Ledger Integrity Monitor - makes LEDGR's own correctness engineering
 * visible. Every row is a real, already-implemented check re-verified from
 * the actual data on every load, not a static list of claims.
 */

import { CheckCircle2, XCircle } from 'lucide-react';

import { Badge, Panel, PanelHeader, Skeleton } from '../ui/primitives';
import { cn } from '../../lib/cn';
import { formatRelative } from '../../lib/format';
import { useIntegrityChecklist } from '../../lib/queries';

export function IntegrityMonitorPanel() {
  const checklist = useIntegrityChecklist();

  return (
    <Panel>
      <PanelHeader
        title="Ledger integrity"
        description="Correctness properties LEDGR enforces, re-verified against the real data."
        actions={
          checklist.data && (
            <Badge tone={checklist.data.all_passed ? 'positive' : 'negative'}>
              {checklist.data.all_passed ? 'All checks passing' : 'Attention needed'}
            </Badge>
          )
        }
      />
      {checklist.isLoading ? (
        <div className="space-y-2 p-4">
          <Skeleton className="h-6 w-full" />
          <Skeleton className="h-6 w-full" />
          <Skeleton className="h-6 w-full" />
        </div>
      ) : (
        <ul className="divide-y divide-border">
          {checklist.data?.checks.map((check) => (
            <li key={check.name} className="flex items-start gap-2.5 px-4 py-2.5">
              {check.passed ? (
                <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-positive" strokeWidth={1.9} />
              ) : (
                <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-negative" strokeWidth={1.9} />
              )}
              <div className="min-w-0">
                <p className={cn('text-[0.875rem]', check.passed ? 'text-ink' : 'text-negative')}>
                  {check.name}
                </p>
                <p className="mt-0.5 text-[0.8125rem] text-ink-3">{check.detail}</p>
              </div>
            </li>
          ))}
        </ul>
      )}
      {checklist.data && (
        <p className="border-t border-border px-4 py-2 text-2xs text-ink-3">
          Checked {formatRelative(checklist.data.checked_at)}
        </p>
      )}
    </Panel>
  );
}
