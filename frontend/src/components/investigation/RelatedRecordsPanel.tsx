/**
 * Everything that points at one transaction: reconciliation results, investigation
 * cases and reviewer decisions - the links an investigator follows - plus an
 * optional AI history explanation (a cited draft; it changes nothing).
 */

import { Sparkles } from 'lucide-react';
import { Link } from 'react-router-dom';

import { useAuth } from '../../lib/auth';
import { formatDateTime } from '../../lib/format';
import { useRelatedRecords, useTransactionHistoryAI } from '../../lib/opsQueries';
import { useAIStatus } from '../../lib/queries';
import type { ReconciliationStatus } from '../../lib/types';
import { CopilotAnswerView, copilotErrorMessage } from '../ai/CopilotPanel';
import { CaseStatusBadge, PriorityBadge, ReconStatusBadge } from '../ops/atoms';
import { Badge, Button, Panel, PanelHeader, Skeleton, Spinner } from '../ui/primitives';
import { ErrorState } from '../ui/Table';

export function RelatedRecordsPanel({ transactionId }: { transactionId: string }) {
  const { can } = useAuth();
  const related = useRelatedRecords(transactionId);
  const aiStatus = useAIStatus();
  const history = useTransactionHistoryAI();
  const data = related.data;
  const empty = data && data.reconciliation.length === 0 && data.investigations.length === 0 && data.decisions.length === 0;

  return (
    <Panel>
      <PanelHeader
        title="Related records"
        description="Reconciliation, investigations and reviewer decisions that reference this transaction"
        actions={
          can('ai:use') &&
          aiStatus.data?.available && (
            <Button
              size="sm"
              icon={history.isPending ? <Spinner /> : <Sparkles className="h-3.5 w-3.5" />}
              disabled={history.isPending}
              onClick={() => history.mutate(transactionId)}
            >
              Explain history (AI)
            </Button>
          )
        }
      />
      {related.isError ? (
        <ErrorState error={related.error} onRetry={() => related.refetch()} context="related records" />
      ) : related.isLoading ? (
        <div className="p-4">
          <Skeleton className="h-12 w-full" />
        </div>
      ) : empty ? (
        <p className="px-4 py-4 text-[0.875rem] text-ink-3">
          Nothing else points at this transaction - no reconciliation result, case or decision.
        </p>
      ) : (
        data && (
          <div className="divide-y divide-border">
            {data.reconciliation.map((r) => (
              <div key={r.result_id} className="flex flex-wrap items-start justify-between gap-2 px-4 py-3">
                <div className="min-w-0">
                  <p className="text-[0.8125rem] font-medium text-ink">
                    Reconciliation run #{r.run_number}
                    {r.external_id && <span className="num text-ink-2"> · {r.source_system} / {r.external_id}</span>}
                  </p>
                  <p className="mt-0.5 text-xs text-ink-3">{r.explanation}</p>
                </div>
                <ReconStatusBadge status={r.status as ReconciliationStatus} />
              </div>
            ))}
            {data.investigations.map((c) => (
              <div key={c.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-3">
                <Link to={`/app/investigations/${c.id}`} className="text-[0.8125rem] font-medium text-accent-ink hover:underline">
                  <span className="num">{c.case_number}</span> · {c.title}
                </Link>
                <span className="flex items-center gap-1.5">
                  <PriorityBadge priority={c.priority} />
                  <CaseStatusBadge status={c.status} />
                </span>
              </div>
            ))}
            {data.decisions.map((d) => (
              <div key={d.id} className="px-4 py-3 text-[0.8125rem]">
                <Badge tone={d.decision === 'CONFIRMED_MATCH' ? 'positive' : 'neutral'}>
                  {d.decision === 'CONFIRMED_MATCH' ? 'Match confirmed' : 'Not a match'}
                </Badge>
                <span className="ml-2 text-ink-2">
                  {d.decided_by} · {formatDateTime(d.created_at)} - “{d.reason}”
                </span>
              </div>
            ))}
          </div>
        )
      )}
      {history.isError && (
        <p role="alert" className="border-t border-border px-4 py-3 text-[0.8125rem] text-negative">
          {copilotErrorMessage(history.error)}
        </p>
      )}
      {history.data && (
        <div className="border-t border-border">
          <CopilotAnswerView answer={history.data} />
        </div>
      )}
    </Panel>
  );
}
